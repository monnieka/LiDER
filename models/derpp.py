from datasets import get_dataset
from utils.buffer import Buffer
from torch.nn import functional as F
from models.utils.continual_model import ContinualModel
from utils.args import *
import torch

from utils.distributed import make_dp

def get_parser() -> ArgumentParser:
    parser = ArgumentParser(description='Continual learning via'
                                        ' Dark Experience Replay++.')
    add_management_args(parser)
    add_experiment_args(parser)
    add_rehearsal_args(parser)
    add_aux_dataset_args(parser)

    parser.add_argument('--alpha', type=float, required=True,
                        help='Penalty weight.')
    parser.add_argument('--beta', type=float, required=True,
                        help='Penalty weight.')

    return parser


class Derpp(ContinualModel):
    NAME = 'derpp'
    COMPATIBILITY = ['class-il', 'domain-il', 'task-il', 'general-continual']

    def __init__(self, backbone, loss, args, transform):
        super(Derpp, self).__init__(backbone, loss, args, transform)

        if args.distributed != 'ddp':
            self.buffer = Buffer(self.args.buffer_size, self.device)
        else:
            import os
            partial_buf_size = self.args.buffer_size // int(os.environ['MAMMOTH_WORLD_SIZE'])
            print('using partial buf size', partial_buf_size)
            self.buffer = Buffer(partial_buf_size, self.device)
        self.current_task = 0
        self.cpt = get_dataset(args).N_CLASSES_PER_TASK

    def begin_task(self, dataset):
        if self.current_task == 0:
            self.load_initial_checkpoint()
            self.reset_classifier()

            if self.args.distributed == "post_bt":
                self.net = make_dp(self.net)
            
    def end_task(self, dataset):
        self.current_task += 1

    def observe(self, inputs: torch.Tensor, labels: torch.Tensor, not_aug_inputs: torch.Tensor, epoch=None):
        labels = labels.long()
        self.opt.zero_grad()
        outputs = self.net(inputs).float()

        loss = self.loss(outputs, labels)

        if not self.buffer.is_empty():
            buf_inputs, _, buf_logits = self.buffer.get_data(
                self.setting.minibatch_size, transform=self.transform)
            buf_outputs = self.net(buf_inputs).float()
            loss += self.args.alpha * F.mse_loss(buf_outputs, buf_logits)

            buf_inputs, buf_labels, _ = self.buffer.get_data(
                self.setting.minibatch_size, transform=self.transform)
            buf_outputs = self.net(buf_inputs).float()
            loss += self.args.beta * self.loss(buf_outputs, buf_labels)

        loss.backward()
        self.opt.step()

        self.buffer.add_data(examples=not_aug_inputs,
                             labels=labels,
                             logits=outputs.data)

        return loss.item(),0,0,0,0
