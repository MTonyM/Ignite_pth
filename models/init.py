import importlib
import os

import torch
import torch.backends.cudnn as cudnn
import torch.nn as nn


def setup(opt, checkpoint):
    optimState = None

    print('=> Creating model from file: models/' + opt.netType + '.py')
    models = importlib.import_module('models.' + opt.netType + '.' + opt.netType)
    model = models.createModel(opt)

    if checkpoint is not None:
        modelPath = os.path.join(opt.resume, checkpoint['modelFile'])
        assert os.path.exists(modelPath), '=> WARNING: Saved model state not found: ' + modelPath
        print('=> Resuming model state from ' + modelPath)
        model.load_state_dict(torch.load(modelPath))
        optimPath = os.path.join(opt.resume, checkpoint['optimFile'])
        assert os.path.exists(optimPath), '=> WARNING: Saved optimState not found: ' + optimPath
        print('=> Resuming optimizer state from ' + optimPath)
        optimState = torch.load(optimPath)

    # if isinstance(model, nn.DataParallel):
    #     model = model.
    if len(opt.GPUs) >= 1:
        model = nn.DataParallel(model, device_ids=eval('[' + opt.GPUs + ']'))


    if opt.cudnn == 'fastest':
        cudnn.fastest = True
        cudnn.benchmark = True
    elif opt.cudnn == 'deterministic':
        pass

    return model, optimState
