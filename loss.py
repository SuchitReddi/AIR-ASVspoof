import torch
import torch.nn as nn
from torch.autograd.function import Function
import torch.nn.functional as F
from torch.autograd import Variable

class LossForOCAnti(nn.Module):
    def __init__(self, target):
        super(LossForOCAnti, self).__init__()
        self.target = target

    def loss(self, ground_truth, predicted):
        raise NotImplementedError("loss has not been implemented")

class CompactnessLoss(LossForOCAnti):
    def __init__(self, target):
        super(CompactnessLoss, self).__init__()

    def loss(self, ground_truth, predicted):
        return 0

class DescriptiveLoss(LossForOCAnti):
    def __init__(self):
        super(DescriptiveLoss, self).__init__()
        self.target = target

    def loss(self, ground_truth, predicted):
        return 0


class CenterLoss(nn.Module):
    def __init__(self, num_classes, feat_dim):
        super(CenterLoss, self).__init__()
        self.num_classes = num_classes
        self.feat_dim = feat_dim
        self.centers = nn.Parameter(torch.randn(num_classes, feat_dim))
        self.centerlossfunction = CenterlossFunction.apply

    def forward(self, feat, y):
        # To squeeze the Tenosr
        batch_size = feat.size(0)
        feat = feat.view(batch_size, 1, 1, -1).squeeze()
        # To check the dim of centers and features
        if feat.size(1) != self.feat_dim:
            raise ValueError("Center's dim: {0} should be equal to input feature's dim: {1}".format(self.feat_dim,feat.size(1)))
        return self.centerlossfunction(feat, y, self.centers)


class CenterlossFunction(Function):

    @staticmethod
    def forward(ctx, feature, label, centers):
        ctx.save_for_backward(feature, label, centers)
        centers_pred = centers.index_select(0, label.long())
        return (feature - centers_pred).pow(2).sum(1).sum(0) / 2.0


    @staticmethod
    def backward(ctx, grad_output):
        feature, label, centers = ctx.saved_variables
        grad_feature = feature - centers.index_select(0, label.long()) # Eq. 3

        # init every iteration
        counts = torch.ones(centers.size(0))
        grad_centers = torch.zeros(centers.size())
        if feature.is_cuda:
            counts = counts.cuda()
            grad_centers = grad_centers.cuda()
        # print counts, grad_centers

        # Eq. 4 || need optimization !! To be vectorized, but how?
        for i in range(feature.size(0)):
            # j = int(label[i].data[0])
            j = int(label[i].item())
            counts[j] += 1
            grad_centers[j] += (centers.data[j] - feature.data[i])
        # print counts
        grad_centers = Variable(grad_centers/counts.view(-1, 1))

        return grad_feature * grad_output, None, grad_centers

class IsolateLoss(nn.Module):
    """Isolate loss.

        Reference:
        I. Masi, A. Killekar, R. M. Mascarenhas, S. P. Gurudatt, and W. AbdAlmageed, “Two-branch Recurrent Network for Isolating Deepfakes in Videos,” 2020, [Online]. Available: http://arxiv.org/abs/2008.03412.
        Args:
            num_classes (int): number of classes.
            feat_dim (int): feature dimension.
        """
    def __init__(self, num_classes=10, feat_dim=2, r_real=0.042, r_fake=1.638):
        super(IsolateLoss, self).__init__()
        self.num_classes = num_classes
        self.feat_dim = feat_dim
        self.r_real = r_real
        self.r_fake = r_fake

        self.center = nn.Parameter(torch.randn(1, self.feat_dim))

    def forward(self, x, labels):
        """
        Args:
            x: feature matrix with shape (batch_size, feat_dim).
            labels: ground truth labels with shape (batch_size).
        """
        batch_size = x.size(0)
        o1 = nn.ReLU()(torch.norm(x-self.center, p=2, dim=1) - self.r_real).unsqueeze(1)
        o2 = nn.ReLU()(self.r_fake - torch.norm(x-self.center, p=2, dim=1)).unsqueeze(1)

        distmat = torch.cat((o1, o2), dim=1)

        classes = torch.arange(self.num_classes).long().cuda()
        # classes = classes.cuda()
        labels = labels.unsqueeze(1).expand(batch_size, self.num_classes)
        mask = labels.eq(classes.expand(batch_size, self.num_classes))

        dist = distmat * mask.float()
        loss = dist.clamp(min=1e-12, max=1e+12).sum(0) / mask.sum(0)

        return loss.sum()


class LGMLoss_v0(nn.Module):
    """
    LGMLoss whose covariance is fixed as Identity matrix
    """
    def __init__(self, num_classes, feat_dim, alpha):
        super(LGMLoss_v0, self).__init__()
        self.feat_dim = feat_dim
        self.num_classes = num_classes
        self.alpha = alpha

        self.centers = nn.Parameter(torch.randn(num_classes, feat_dim))

    def forward(self, feat, label):
        batch_size = feat.shape[0]

        diff = torch.unsqueeze(feat, dim=1) - torch.unsqueeze(self.centers, dim=0)
        diff = torch.mul(diff, diff)
        dist = torch.sum(diff, dim=-1)

        y_onehot = torch.FloatTensor(batch_size, self.num_classes)
        y_onehot.zero_()
        y_onehot = Variable(y_onehot).cuda()
        y_onehot.scatter_(1, torch.unsqueeze(label, dim=-1), self.alpha)
        y_onehot = y_onehot + 1.0
        margin_dist = torch.mul(dist, y_onehot)
        margin_logits = -0.5 * margin_dist
        logits = -0.5 * dist

        cdiff = feat - torch.index_select(self.centers, dim=0, index=label.long())
        likelihood = (1.0/batch_size) * cdiff.pow(2).sum(1).sum(0) / 2.0
        return logits, margin_logits, likelihood

class LMCL_loss(nn.Module):
    """
        Refer to paper:
        Hao Wang, Yitong Wang, Zheng Zhou, Xing Ji, Dihong Gong, Jingchao Zhou,Zhifeng Li, and Wei Liu
        CosFace: Large Margin Cosine Loss for Deep Face Recognition. CVPR2018
        re-implement by yirong mao
        2018 07/02
        """

    def __init__(self, num_classes, feat_dim, s=7.00, m=0.2):
        super(LMCL_loss, self).__init__()
        self.feat_dim = feat_dim
        self.num_classes = num_classes
        self.s = s
        self.m = m
        self.centers = nn.Parameter(torch.randn(num_classes, feat_dim))

    def forward(self, feat, label):
        batch_size = feat.shape[0]
        norms = torch.norm(feat, p=2, dim=-1, keepdim=True)
        nfeat = torch.div(feat, norms)

        norms_c = torch.norm(self.centers, p=2, dim=-1, keepdim=True)
        ncenters = torch.div(self.centers, norms_c)
        logits = torch.matmul(nfeat, torch.transpose(ncenters, 0, 1))

        y_onehot = torch.FloatTensor(batch_size, self.num_classes)
        y_onehot.zero_()
        y_onehot = Variable(y_onehot).cuda()
        y_onehot.scatter_(1, torch.unsqueeze(label, dim=-1), self.m)
        margin_logits = self.s * (logits - y_onehot)

        return logits, margin_logits


if __name__ == "__main__":
    feats = torch.randn((32, 90)).cuda()
    center = torch.randn((1,90)).cuda()
    o = torch.norm(feats - center, p=2, dim=1)
    print(o.shape)
    # dist = torch.cat((o, o), dim=1)
    # print(dist.shape)
    labels = torch.cat((torch.Tensor([0]).repeat(10).unsqueeze(1),
                       torch.Tensor([1]).repeat(22).unsqueeze(1)),0).cuda()
    # classes = torch.arange(2).long().cuda()
    # labels = labels.expand(32, 2)
    # print(labels)
    # mask = labels.eq(classes.expand(32, 2))
    # print(mask)

    # iso_loss = IsolateLoss(2, 90).cuda()
    # loss = iso_loss(feats, labels)
    # print(loss.shape)