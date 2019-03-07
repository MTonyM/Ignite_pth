import os
import time

import torch
import torch.optim as optim
from torch.autograd import Variable

from util.progbar import progbar
from util.utils import RunningAverage
import numpy as np

# allAcc = {}
# for metric in trainAcc:
#     allAcc[metric + "_train"] = trainAcc[metric]
#     allAcc[metric + "_val"] = testAcc[metric]
#
# bb.writer.add_scalars(opt.suffix + '/scalar/Loss', {'Loss_train': trainLoss, 'Loss_val': testLoss}, epoch)
# bb.writer.add_scalars(opt.suffix + '/scalar/Acc', allAcc, epoch)
# bb.writer.add_scalars(opt.suffix + '/scalar/LR', {'LR': float(trainer.scheduler.get_lr()[0])}, epoch)


class Trainer:
    def __init__(self, model, criterion, metrics, opt, optimState, bb):
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

        self.bb = bb
        self.bb_suffix = opt.suffix

    def train(self, trainLoader, epoch):
        self.model.train()
        print("=> Training epoch")

        # =====
        avgLoss = RunningAverage()
        avgAcces = {}
        for metric in self.metrics.name:
            avgAcces[metric] = RunningAverage()
        self.progbar = progbar(len(trainLoader), width=self.opt.barwidth)
        # =====

        for i, (input, target) in enumerate(trainLoader):
            logger_idx = epoch - 1 + i * 1.0 / len(trainLoader)
            if self.opt.debug and i > 1:  # check debug.
                break
            start = time.time()

            inputV, targetV = Variable(input), Variable(target)
            if self.opt.GPU:
                inputV = inputV.cuda()
                targetV = targetV.cuda()

            output = self.model(inputV)
            output = torch.softmax(output, dim=1)

            # _reshape = output.shape[2]*output.shape[3]

            # output.view(*output.shape[:2], _reshape)
            # targetV.view(targetV.shape[0], _reshape)
            loss = self.criterion(output, targetV.long())

            self.optimizer.zero_grad()
            loss.mean().backward()


            _, preds = torch.max(output, 1)
            self.optimizer.step()

            # LOG ===
            runTime = time.time() - start
            runningLoss = torch.mean(loss).data.cpu().numpy()
            if not np.isnan(runningLoss):
                self.bb.writer.add_scalars(self.bb_suffix + '/scalar/Loss', {'Loss_train': runningLoss}, logger_idx)
                avgLoss.update(float(runningLoss))
            logAcc = []
            for metric in self.metrics.name:
                meTra = self.metrics[metric](preds.detach().cpu().numpy(), targetV.detach().cpu().numpy())
                if isinstance(meTra, dict):
                    newTra = dict()
                    for key in meTra.keys():
                        newTra[key + 'train'] = meTra[key]
                    self.bb.writer.add_scalars(self.bb_suffix + '/scalar/' + metric, newTra, logger_idx)
                else:
                    self.bb.writer.add_scalars(self.bb_suffix + '/scalar/mIoU', {metric + '_train': meTra}, logger_idx)
                    avgAcces[metric].update(meTra)
                    logAcc.append((metric, avgAcces[metric]()))

            log = updateLog(epoch, i, len(trainLoader), runTime, avgLoss(), avgAcces)
            self.logger['train'].write(log)
            self.progbar.update(i, [('Time', runTime), ('loss', avgLoss()), *logAcc])
            # END LOG ===

        log = '\n* Finished training epoch # %d  Loss: %1.4f  ' % (epoch, avgLoss())
        for metric in avgAcces:
            log += metric + " %1.4f  " % avgAcces[metric]()
        log += '\n'
        self.logger['train'].write(log)
        print(log)
        Accs = {}
        for metric in self.metrics.name:
            Accs[metric] = avgAcces[metric]()
        return avgLoss(), Accs

    def test(self, trainLoader, epoch):
        self.model.eval()
        print("=> Validating epoch")
        avgLoss = RunningAverage()
        avgAcces = {}
        for metric in self.metrics.name:
            avgAcces[metric] = RunningAverage()
        self.progbar = progbar(len(trainLoader), width=self.opt.barwidth)
        for i, (input, target) in enumerate(trainLoader):
            logger_idx = epoch - 1 + i * 1.0 / len(trainLoader)
            if self.opt.debug and i > 1:  # check debug.
                break
            start = time.time()
            with torch.no_grad():
                inputV, targetV = Variable(input), Variable(target)
            if self.opt.GPU:
                inputV, targetV = inputV.cuda(), targetV.cuda()

            with torch.no_grad():
                output = self.model(inputV)
                output = torch.softmax(output, dim=1)

                loss = self.criterion(output, targetV.long())
            _, preds = torch.max(output, 1)
            # LOG ===
            runTime = time.time() - start
            runningLoss = torch.mean(loss).data.cpu().numpy()
            #
            if not np.isnan(runningLoss):
                self.bb.writer.add_scalars(self.bb_suffix + '/scalar/Loss', { 'Loss_val': runningLoss}, logger_idx)
                avgLoss.update(float(runningLoss))

            logAcc = []
            for metric in self.metrics.name:
                meVal = self.metrics[metric](preds.detach().cpu().numpy(), targetV.detach().cpu().numpy())
                if isinstance(meVal, dict):
                    newVal = dict()
                    for key in meVal.keys():
                        newVal[key + 'val'] = meVal[key]
                    self.bb.writer.add_scalars(self.bb_suffix + '/scalar/' + metric, newVal, logger_idx)
                else:
                    self.bb.writer.add_scalars(self.bb_suffix + '/scalar/mIoU', {metric + '_val': meVal}, logger_idx)
                    avgAcces[metric].update(meVal)
                    logAcc.append((metric, avgAcces[metric]()))

            log = updateLog(epoch, i, len(trainLoader), runTime, avgLoss(), avgAcces)
            self.logger['val'].write(log)
            self.progbar.update(i, [('Time', runTime), ('loss', avgLoss()), *logAcc])
            # END LOG ===

        log = '\n* Finished test epoch # %d  Loss: %1.4f ' % (epoch, avgLoss())
        for metric in avgAcces:
            log += metric + " %1.4f  " % avgAcces[metric]()
        log += '\n'
        self.logger['val'].write(log)
        print(log)
        Accs = {}
        for metric in self.metrics.name:
            Accs[metric] = avgAcces[metric]()
        return avgLoss(), Accs

    def LRDecay(self, epoch):
        self.scheduler = optim.lr_scheduler.ExponentialLR(self.optimizer, gamma=0.95, last_epoch=epoch - 2)

    def LRDecayStep(self):
        self.scheduler.step()


def updateLog(epoch, i, length, time, err, Acc):
    log = 'Epoch: [%d][%d/%d] Time %1.3f Err %1.4f   ' % (
        epoch, i, length, time, err)
    for metric in Acc:
        log += metric + " %1.4f  " % Acc[metric]()
    log += '\n'
    return log


def createTrainer(model, criterion, metric, opt, optimState, bb):
    return Trainer(model, criterion, metric, opt, optimState, bb)
