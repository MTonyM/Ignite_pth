import os
import time

import torch
import torch.optim as optim
from torch.autograd import Variable
import numpy as np
from util.summaries import BoardX
from util.utils import StoreArray
import SimpleITK as sitk
from util.utils import RunningAverageDict
from PIL  import  Image
from util.evaluation import modelsize
# allAcc = {}
# for metric in trainAcc:
#     allAcc[metric + "_train"] = trainAcc[metric]
#     allAcc[metric + "_val"] = testAcc[metric]
#
# bb.writer.add_scalars(opt.suffix + '/scalar/Loss', {'Loss_train': trainLoss, 'Loss_val': testLoss}, epoch)
# bb.writer.add_scalars(opt.suffix + '/scalar/Acc', allAcc, epoch)
# bb.writer.add_scalars(opt.suffix + '/scalar/LR', {'LR': float(trainer.scheduler.get_lr()[0])}, epoch)


class Trainer:
    def __init__(self, model, criterion, metrics, opt, optimState):
        self.model = model
        self.criterion = criterion
        self.optimState = optimState
        self.opt = opt
        self.metrics = metrics
        if opt.optimizer == 'SGD':
            self.optimizer = optim.SGD(model.parameters(), lr=opt.LR, momentum=opt.momentum, dampening=opt.dampening,
                                       weight_decay=opt.weightDecay)
        elif opt.optimizer == 'Adam':
            self.optimizer = optim.Adam(model.parameters(), lr=opt.LR, betas=(opt.momentum, 0.999), eps=1e-8,
                                        weight_decay=opt.weightDecay)

        if self.optimState is not None:
            self.optimizer.load_state_dict(self.optimState)

        self.logger = {'train': open(os.path.join(opt.resume, 'train.log'), 'a+'),
                       'val': open(os.path.join(opt.resume, 'test.log'), 'a+')}

        self.bb = BoardX(opt, self.metrics, opt.hashKey, self.opt.logNum)
        self.bb_suffix = opt.hashKey
        self.log_num = opt.logNum
        self.www = opt.www


    def processing(self, dataloader, epoch, split, eval):
        # dataloader must holds 3-axis...
        dataloaderx, dataloadery, dataloaderz = dataloader
        loss_y, _ = self.processing_one_branch(dataloadery, epoch, split, eval, 'y')
        loss_z, _ = self.processing_one_branch(dataloaderz, epoch, split, eval, 'z')
        loss_x, set_ = self.processing_one_branch(dataloaderx, epoch, split, eval, 'x')

        output_path_x = self.www + '/Pred_x_' + split
        output_path_y = self.www + '/Pred_y_' + split
        output_path_z = self.www + '/Pred_z_' + split
        gt_path = self.www + '/GT_' + split
        set_ = sorted(list(set(set_)))

        if epoch % 5 == 0 and split == 'val':
            #  ------------ eval for 3d ------------
            hdf = sitk.HausdorffDistanceImageFilter()
            dicef = sitk.LabelOverlapMeasuresImageFilter()
            HDdict_mean = RunningAverageDict()
            dicedict_mean = RunningAverageDict()
            # for instance in set_:
            for instance in set_:
                print(instance)
                pred_x = np.load(os.path.join(output_path_x, instance + '.npy'))
                pred_y = np.load(os.path.join(output_path_y, instance + '.npy'))
                pred_z = np.load(os.path.join(output_path_z, instance + '.npy'))
                pred = self.make_pred(pred_x, pred_y, pred_z)

                gt = np.load(os.path.join(gt_path, instance + '.npy'))
                # Post Processing.
                # DenseCRF

                # simpleITK HD dice
                pred = sitk.GetImageFromArray(pred)
                gt = sitk.GetImageFromArray(gt)
                HDdict = {}
                dicedict = {}
                for i in range(self.opt.numClasses - 1):
                    HD = np.nan
                    dice = np.nan
                    try:
                        hdf.Execute(pred == i + 1, gt == i + 1)
                        HD = hdf.GetHausdorffDistance()
                    except:
                        pass
                    try:
                        dicef.Execute(pred == i + 1, gt == i + 1)
                        dice = dicef.GetDiceCoefficient()
                    except:
                        pass
                    HDdict['HD#' + str(i)] = HD
                    dicedict['Dice#' + str(i)] = dice
                HDdict_mean.update(HDdict)
                dicedict_mean.update(dicedict)
                print("-------------------------")
                print(instance, HDdict, dicedict)
                del pred, gt
            # calculate mean
            self.bb.writer.add_scalars(self.opt.hashKey + '/scalar/HD3d_{}/'.format(split), HDdict_mean(), epoch)
            self.bb.writer.add_scalars(self.opt.hashKey + '/scalar/dice3d_{}/'.format(split), dicedict_mean(), epoch)

        return (loss_x+loss_y+loss_z) /  3

    def make_pred(self, pred_x, pred_y, pred_z):
        print(pred_x.shape, pred_y.shape, pred_z.shape)
        c, h, w, z = pred_z.shape
        pred_x_real = []
        for i in range(h): #252
            cc = []
            for j in range(c):
                pred_x_slice = Image.fromarray(pred_x[j, i, :, :])  # 316 x 180
                pred_x_slice = pred_x_slice.resize((z, 316))
                cc.append(np.array(pred_x_slice))
            cc = np.stack(cc, 0)
            pred_x_real.append(cc)
        pred_x_real = np.stack(pred_x_real, 0)
        pred_x_real = pred_x_real.transpose([1, 0, 2, 3])

        pred_y_real = []
        for i in range(h): #252
            cc = []
            for j in range(c):
                pred_y_slice = Image.fromarray(pred_y[j, i, :, :])
                pred_y_slice = pred_y_slice.resize((z, 316))
                cc.append(np.array(pred_y_slice))
            cc = np.stack(cc, 0)
            pred_y_real.append(cc)
        pred_y_real = np.stack(pred_y_real, 0)
        pred_y_real = pred_y_real.transpose([1, 0, 2, 3])
        print(pred_y_real.shape, pred_x_real.shape, pred_z.shape)
        pred = pred_z + (pred_x_real+ pred_y_real) / 2
        pred = np.argmax(pred, 0)
        print(pred.shape)
        return pred


    def processing_one_branch(self, dataloader, epoch, split, eval, branch='z'):
        store_array_pred = StoreArray(len(dataloader), self.www + '/Pred_' + branch + '_' + split)
        store_array_gt = StoreArray(len(dataloader), self.www + '/GT_' +split)
        # use store utils to update output.
        print('=> {}ing epoch # {} in branch {}'.format(split, epoch, branch))
        is_train = split == 'train'
        is_eval = eval
        if is_train:
            self.model.train()
        else:  # VAL
            self.model.eval()
        self.bb.start(len(dataloader))
        processing_set = []
        for i, ((pid, sid), inputs, target, h) in enumerate(dataloader):
            # self.bb.writer.add_image('{},#{}gt'.format(pid[0],sid[0]), target[0] * 63)
            # self.bb.writer.add_image('{},#{}img'.format(pid[1],sid[1]), inputs[1] * 63)
            if inputs.shape[0] == 1:
                continue
            if self.opt.debug and i > 2:
                break
            # store the patients processed in this phase.
            # print(branch, inputs.shape)
            processing_set += pid
            start = time.time()
            # * Data preparation *
            if self.opt.GPU:
                inputs, target, h = inputs.cuda(), target.cuda(), h.cuda()
            inputV, targetV, hV = Variable(inputs).float(), Variable(target), Variable(h).float()
            datatime = time.time() - start
            # * Feed in nets*
            if is_train:
                self.optimizer.zero_grad()
            output = self.model((inputV, hV), branch)
            loss, loss_record = self.criterion(output, targetV.long())
            if is_train:
                loss.mean().backward()
                self.optimizer.step()
            # * Eval *
            metrics = {}
            with torch.no_grad():
                _, preds = torch.max(output, 1)
                if is_eval:
                    metrics = self.metrics(preds, targetV)
            if epoch % 5 == 0 and split == 'val':
                # save each slice ...
                store_array_pred.update(pid, sid, output.detach().cpu().numpy(), branch)
                if branch == 'z':
                    store_array_gt.update(pid, sid, targetV.detach().cpu().numpy(), branch)

            runTime = time.time() - start - datatime
            log = self.bb.update(loss_record, {'TD': datatime, 'TR': runTime}, metrics, split, i, epoch, branch)
            del loss, loss_record, output
            self.logger[split].write(log)

        self.logger[split].write(self.bb.finish(epoch, split))
        if epoch % 5 == 0 and split == 'val':
            store_array_pred.save_zzz(branch)
            if branch == 'z':
                store_array_gt.save(branch)
        del store_array_pred, store_array_gt
        return self.bb.avgLoss()['loss'], processing_set

    def train(self, dataLoader, epoch):
        loss = self.processing(dataLoader, epoch, 'train', True)
        return loss

    def test(self, dataLoader, epoch):
        loss = self.processing(dataLoader, epoch, 'val', True)
        return loss

    def LRDecay(self, epoch):
        # poly_scheduler.adjust_lr(self.optimizer, epoch)
        self.scheduler = optim.lr_scheduler.ExponentialLR(self.optimizer, gamma=0.95, last_epoch=epoch - 2)

    def LRDecayStep(self):
        self.scheduler.step()


class poly_scheduler:
    def __init__(self, opt):
        self.opt = opt

    def adjust_lr(self, optimizer, epoch):
        lr = self.opt.LR * (1 - epoch / self.opt.nEpochs) ** 0.9
        for param_group in optimizer.param_groups:
            param_group['lr'] = lr


def createTrainer(model, criterion, metric, opt, optimState):
    return Trainer(model, criterion, metric, opt, optimState)
