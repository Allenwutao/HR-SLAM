# -*- coding: utf-8 -*-

__author__ = 'smh'
__date__ = '2018.07.05'

import mxnet as mx
from mxnet import gluon
from mxnet.ndarray.contrib import BilinearResize2D
from mxnet import nd
import numpy as np
from mxnet import autograd

# 参考文献：RedNet: Residual Encoder-Decoder Network for indoor RGB-D Semantic Segmentation, 2018.06.04 arxiv
# Cautions:  backbone network: ResNet-50 (RedNet)
# Evaluation References: [1]. FuseNet ...,  [2]. FCN, [3]. Github: py_img_seg_evl

# 文档结构： (1). RedNet Model, (2). Loss class, (3). Evaluation Metrics, (4). Training function, etc.


def stnlayer(data):
    #net = gluon.nn.HybridSequential()
    return mx.sym.SpatialTransformer(data, transform_type='affine')


def get_convlayer(output_num, ks, s, p=0, bn=True, act=False):
    net = gluon.nn.HybridSequential()
    net.add(
        gluon.nn.Conv2D(output_num, kernel_size=ks, strides=s, padding=p, use_bias=False),
    )
    if bn:
        net.add(gluon.nn.BatchNorm())

    if act:
        net.add(gluon.nn.Activation('relu'))

    return net


def get_reslayer_upsample(output_num, ks=4, s=2, p=1, act=False):
    net = gluon.nn.HybridSequential()
    net.add(
        gluon.nn.Conv2DTranspose(output_num, kernel_size=ks, strides=s, padding=p, use_bias=False, weight_initializer=mx.init.Bilinear()),
        gluon.nn.BatchNorm()
    )

    if act:
        net.add(gluon.nn.Activation('relu'))

    return net


# Green Block: residual layer with no downsample
class resblock_same(gluon.nn.HybridBlock):
    def __init__(self, output_nums, **kwargs):
        super(resblock_same, self).__init__()

        mid_output_nums = output_nums >> 2     # the first two layer in a bottleneck of resdual layer is 1/4 input channels
        self.conv1 = get_convlayer(mid_output_nums, ks=3, s=1, p=1, act=True)
        self.conv2 = get_convlayer(mid_output_nums, ks=3, s=1, p=1, act=True)
        self.conv3 = get_convlayer(output_nums, ks=3, s=1, p=1, act=False)
        self.conv1_side = get_convlayer(output_nums, ks=1, s=1, p=0, act=False)

    def hybrid_forward(self, F, x, *args, **kwargs):
        conv1 = self.conv3(self.conv2(self.conv1(x)))
        # 无论是ResNet还是Identity ResNet都是通过一个旁路来保证channel的一致，Identity ResNet文章说，
        # 这样的旁路不多，可以忽略，或者文章中的推导可以认为是适用于相同size的feature map而言的。
        conv1_side = self.conv1_side(x)
        return F.relu(conv1_side + conv1)


# 即普通的具有bottleneck结构的res block
class bottleneck(gluon.nn.HybridBlock):
    def __init__(self, output_nums, **kwargs):
        super(bottleneck, self).__init__(**kwargs)

        mid_output_nums = output_nums >> 2     # the first two layer in a bottleneck of resdual layer is 1/4 input channels
        self.conv1 = get_convlayer(mid_output_nums, ks=1, s=1, p=0, act=True)
        self.conv2 = get_convlayer(mid_output_nums, ks=3, s=1, p=1, act=True)
        self.conv3 = get_convlayer(output_nums, ks=1, s=1, act=False)

    def hybrid_forward(self, F, x, *args, **kwargs):
        conv1 = self.conv3(self.conv2(self.conv1(x)))

        return F.relu(x + conv1)


# Orange Block: ResLayer with Downsample
# the pattern of resnet-50
class resblock_downsample(gluon.nn.HybridBlock):
    def __init__(self, output_nums, **kwargs):
        super(resblock_downsample, self).__init__(**kwargs)

        mid_output_nums = output_nums >> 2    # output channel numbers of the first conv layer
        #lst_output_nums = output_nums    # output channel numbers of the last conv layer
        self.conv1 = get_convlayer(mid_output_nums, ks=1, s=1, act=True)
        self.conv2 = get_convlayer(mid_output_nums, ks=3, s=2, p=1, act=True)         # the stride = 2, dimension halfed
        self.conv3 = get_convlayer(output_nums, ks=1, s=1, act=False)
        self.conv1_side = get_convlayer(output_nums, ks=1, s=2, act=False)

    def hybrid_forward(self, F, x, *args, **kwargs):
        conv1 = self.conv3(self.conv2(self.conv1(x)))
        conv1_side = self.conv1_side(x)
        return F.relu(conv1 + conv1_side)


# Pink Block: ResLayer with Upsample
class resblock_upsample(gluon.nn.HybridBlock):
    def __init__(self, output_nums, **kwargs):
        super(resblock_upsample, self).__init__(**kwargs)

        mid_output_nums = output_nums << 1     # the output channels is halfed

        self.conv1 = get_convlayer(mid_output_nums, ks=3, s=1, p=1, act=True)
        self.deconv1 = get_reslayer_upsample(output_nums)               # upsample the dimension & half the channels
        self.deconv1_side = get_reslayer_upsample(output_nums)

    def hybrid_forward(self, F, x, *args, **kwargs):
        upconv1 = self.deconv1(self.conv1(x))
        upconv1_side = self.deconv1_side(x)

        return F.relu(upconv1 + upconv1_side)


# 文章说了，在encoder部分的layer1~4里面，每层layer只有一层是具有降维功能的res layer with downsample， 其他层是具有bottleneck的res block(见ResNet原文)
# 注意，在每一个layer中，模型是先进行降维(一层res layer with downsample)，然后后面在跟几个不降维的res block (unit)
def get_resblock_encoder(output_nums, unit_nums, down=True):
    blk = gluon.nn.HybridSequential()

    if down:       # encoder中的除了第一层layer之外的layer的结构，先进行降维，然后后面跟着几个具有bottleneck的res block
        blk.add(resblock_downsample(output_nums))       # 利用res layer with downsample首先进行降维
        for i in range(unit_nums - 1):
            blk.add(bottleneck(output_nums))
    else:                                               # 这里主要用于实现encoder里面的第一层layer，即不降维
        blk.add(resblock_same(output_nums))                 # 但是block之间存在channel的翻倍关系，通过旁落来保证channel一致
        for i in range(unit_nums - 1):                      # 不降维的话，就是普通的绿色block, 即全是具有bottleneck的res block
            blk.add(bottleneck(output_nums))
    return blk


# 文章说了，在decoder部分的trans1-5里面，每层trans只有一层是具有升维功能的res layer with upsample， 其它层是具有普通两层3*3卷积同等输出channel的res block.
# 在decoder的最后一层是一层Conv2DTranspose实现，而不是包含res layer with upsample的trans层实现的
# 注意，与encoder部分不同的是，在每一个trans结构里面，是先存在几个不降维的res block (unit)，在最后才会跟一层 res layer with upsample
def get_resblock_decoder(output_nums, unit_nums, dimskeep=False, up=True):
    blk = gluon.nn.HybridSequential()
    if up:
        if dimskeep:
            for i in range(unit_nums-1):
                blk.add(bottleneck(output_nums))
            blk.add(resblock_upsample(output_nums))
        else:
            for i in range(unit_nums-1):
                blk.add(
                    bottleneck(output_nums << 1)     # 这里与原文不同，原文这里并没有采用bottleneck结构，而是普通的两层3*3同等输出channel数量的卷积层，见ResNet论文,为了方便，这里采用bottleneck 结构
                )
            blk.add(resblock_upsample(output_nums))
    else:                                       # else分支作为实现Trans5的基础，因为Trans5并没有升维，而是配合后续的一个Transpose Conv layer来实现升维！
        if dimskeep:
            for i in range(unit_nums - 1):
                blk.add(
                    bottleneck(output_nums)     # 这里与原文不同，原文这里并没有采用bottleneck结构，而是普通的两层3*3同等输出channel数量的卷积层，见ResNet论文,为了方便，这里采用bottleneck 结构
                )
            blk.add(resblock_same(output_nums))
        else:
            for i in range(unit_nums - 1):
                blk.add(
                    bottleneck(output_nums << 1)     # 这里与原文不同，原文这里并没有采用bottleneck结构，而是普通的两层3*3同等输出channel数量的卷积层，见ResNet论文,为了方便，这里采用bottleneck 结构
                )
            blk.add(resblock_same(output_nums))
    return blk


# 这里采用的是res-50结构
# 在encoder部分共降维32倍，在decoder部分共升维32倍。
# 在res-50作为encoder时，采用了channel扩充的思想，也就是在encoder部分的channel数量比较大；为了降低存储需求，所以在decoder部分采用的channel数量就变得很小。
# 由于存在encoder到decoder部分的pass连接，但二者的channel数量不同，所以模型中又加入了Agent(1*1的卷积核)结构用于对encoder部分进行project到较少channel的数量
# 在encoder里面的layer1是没有降维的！这一块是配合前一层模型中唯一的max pooling with stride=2来实现的降维，文章中这一层layer是绿色的，说明不降维，橘黄色的encoder layer是自带降维
# 所有的fusion操作是 Element-wise Addition
class ASENet(gluon.nn.HybridBlock):
    def __init__(self, class_nums, **kwargs):
        super(ASENet, self).__init__(**kwargs)
        self.class_nums = class_nums

        # Encoder Part
        # covolution layer 1 with stride = 2
        self.conv1 = get_convlayer(64, ks=7, s=2, p=3, act=True)
        self.conv1_d = get_convlayer(64, ks=7, s=2, p=3, act=True)
        # max pooling layer 1 with stride = 2
        self.maxpool1 = gluon.nn.MaxPool2D(pool_size=3, strides=2, padding=1)
        self.maxpool1_d = gluon.nn.MaxPool2D(pool_size=3, strides=2, padding=1)
        # block layer 1 of encoder part
        self.encoder_layer1 = get_resblock_encoder(256, 3, down=False)          # 这里配合上面的maxpool1(_d)实现降维
        self.encoder_layer1_d = get_resblock_encoder(256, 3, down=False)
        # block layer 2 of encoder part
        self.encoder_layer2 = get_resblock_encoder(512, 4)
        self.encoder_layer2_d = get_resblock_encoder(512, 4)
        # block layer 3 of encoder part
        self.encoder_layer3 = get_resblock_encoder(1024, 6)
        self.encoder_layer3_d = get_resblock_encoder(1024, 6)
        # block layer 4 of encoder part
        self.encoder_layer4 = get_resblock_encoder(2048, 3)
        self.encoder_layer4_d = get_resblock_encoder(2048, 3)

        # Decoder Part
        # No need for depth path
        self.trans1 = get_resblock_decoder(256, 6)
        self.trans2 = get_resblock_decoder(128, 4)
        self.trans3 = get_resblock_decoder(64, 3)
        self.trans4 = get_resblock_decoder(64, 3, dimskeep=True)
        self.trans5 = get_resblock_decoder(64, 3, dimskeep=True, up=False)
        self.finalconv = gluon.nn.Conv2DTranspose(channels=class_nums, kernel_size=4, strides=2, padding=1, use_bias=False, weight_initializer=mx.init.Bilinear())

        # Agents Part
        self.agent4 = get_convlayer(512, ks=1, s=1, act=True)
        self.agent3 = get_convlayer(256, ks=1, s=1, act=True)
        self.agent2 = get_convlayer(128, ks=1, s=1, act=True)
        self.agent1 = get_convlayer(64, ks=1, s=1, act=True)
        self.agent0 = get_convlayer(64, ks=1, s=1, act=True)

        # Side Outputs Part
        self.conv_side_output1 = get_convlayer(class_nums, ks=1, s=1, bn=False, act=False)
        self.conv_side_output2 = get_convlayer(class_nums, ks=1, s=1, bn=False, act=False)
        self.conv_side_output3 = get_convlayer(class_nums, ks=1, s=1, bn=False, act=False)
        self.conv_side_output4 = get_convlayer(class_nums, ks=1, s=1, bn=False, act=False)

    def hybrid_forward(self, F, rgb, depth, *args, **kwargs):
        # Encoder Part
        conv1 = self.conv1(rgb)
        conv1_d = self.conv1_d(depth)

        layer1 = self.encoder_layer1(self.maxpool1(conv1 + conv1_d))
        layer1_d = self.encoder_layer1_d(self.maxpool1_d(conv1_d))

        layer2 = self.encoder_layer2(layer1 + layer1_d)
        layer2_d = self.encoder_layer2_d(layer1_d)

        layer3 = self.encoder_layer3(layer2 + layer2_d)
        layer3_d = self.encoder_layer3_d(layer2_d)

        layer4 = self.encoder_layer4(layer3 + layer3_d)
        layer4_d = self.encoder_layer4_d(layer3_d)

        # Agent Part
        agent4 = self.agent4(layer4 + layer4_d)
        agent3 = self.agent3(layer3 + layer3_d)
        agent2 = self.agent2(layer2 + layer2_d)
        agent1 = self.agent1(layer1 + layer1_d)
        agent0 = self.agent0(conv1 + conv1_d)

        # Decoder Part
        trans1 = self.trans1(agent4)
        trans2 = self.trans2(agent3 + trans1)
        trans3 = self.trans3(agent2 + trans2)
        trans4 = self.trans4(agent1 + trans3)
        finaloutput = self.finalconv(self.trans5(agent0 + trans4))

        # Side Output Part
        side_output1 = self.conv_side_output1(trans1 + agent3)
        side_output2 = self.conv_side_output2(trans2 + agent2)
        side_output3 = self.conv_side_output3(trans3 + agent1)
        side_output4 = self.conv_side_output4(trans4 + agent0)

        # Return all outputs
        return finaloutput, side_output4, side_output3, side_output2, side_output1


# 当对side output进行计算loss时，需要将输入的label利用nearset-neighbor算法进行降维
class target_loss(gluon.loss.Loss):
    def __init__(self, weight=None, axis=1, **kwargs):
        super(target_loss, self).__init__(weight, axis, **kwargs)
        self.loss = gluon.loss.SoftmaxCrossEntropyLoss(axis=axis)   # along the channel axis

    def hybrid_forward(self, F, pred, label, *args, **kwargs):
        return self.loss(pred, label)


def generate_target(label):
    label = nd.expand_dims(label, axis=1)
    label_size = label.shape
    #print('label_size = ', label_size)   # (2, 640, 480)
    output4_size = (label_size[2] >> 1, label_size[3] >> 1)
    output3_size = (label_size[2] >> 2, label_size[3] >> 2)
    output2_size = (label_size[2] >> 3, label_size[3] >> 3)
    output1_size = (label_size[2] >> 4, label_size[3] >> 4)

    label_output4 = BilinearResize2D(label, *output4_size)
    label_output3 = BilinearResize2D(label, *output3_size)
    label_output2 = BilinearResize2D(label, *output2_size)
    label_output1 = BilinearResize2D(label, *output1_size)

    #print('type of label_output: ', type(label_output4))
    #print('shape of label_output: ', label_output4.shape)

    return label, label_output4, label_output3, label_output2, label_output1


loss_inst = target_loss()


class ShapeError(Exception):
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)


def check_shapes(labels, preds, shape=True):
    if shape:
        labels_shape, preds_shape = labels.shape, preds.shape
    else:
        labels_shape, preds_shape = len(labels), len(preds)

    if labels_shape != preds_shape:
        raise  ShapeError('shape of labels {} does not match shape of predictions {}.'.format(labels_shape, preds_shape))

    return labels, preds


#! \param n_cl the number of classes
def extract_mask(seg, n_cl):
    h, w = seg.shape
    masks = np.zeros(n_cl, h, w)

    for i, c in enumerate(range(n_cl)):
        masks[i, :, :] = (seg == c)

    return masks


# Three evaluation metrices usually used in image segmentation
# IoU: TP / (TP + FP + FN)
# mIoU: average over all classes
class mIoU(mx.metric.EvalMetric):
    def __init__(self, num_classes, name = 'miou', axis=0, **kwargs):
        super(mIoU, self).__init__(name, **kwargs)
        self.sum_metric = 0.0
        self.num_classes = num_classes
        self.axis = axis

    def update(self, labels, preds):   # only consider the origin size labels and predicts
        for label, pred in zip(labels, preds):
            pred = nd.argmax(pred, axis=self.axis)   # note that the default axis is 0
            check_shapes(label, pred)
            if isinstance(label, nd.NDArray):
                label = label.asnumpy().astype('int32')
            if isinstance(pred, nd.NDArray):
                pred = pred.asnumpy().astype('int32')
            pred_mask = extract_mask(pred, self.num_classes)
            label_mask = extract_mask(label, self.num_classes)
            miou = list([0]) * self.num_classes
            for i, c in enumerate(range(self.num_classes)):
                curr_pred_mask = pred_mask[i, :, :]
                curr_label_mask = label_mask[i, :, :]
                if (np.sum(curr_pred_mask) == 0) or (np.sum(curr_label_mask) == 0):
                    continue    # 图像中不含有这一类目标 ! !
                n_ii = np.sum(curr_label_mask == curr_pred_mask)
                t_i = np.sum(curr_label_mask)    # ground truth
                n_ij = np.sum(curr_pred_mask)
                miou[i] = n_ii / (t_i + n_ij - n_ii)
            self.sum_metric += np.sum(miou[i]) / self.num_classes
            self.num_inst += 1   # the number of samples


# pixel accuracy = (\sum_c^{K}TP_c) / N_{pixel}
# equals to normal accuracy, i.e. the background is took as class '0'
class PixelAccuracy(mx.metric.EvalMetric):
    def __init__(self, num_classes, name='pa', axis=0, **kwargs):
        super(PixelAccuracy, self).__init__(name, **kwargs)
        self.num_classes = num_classes
        self.axis = axis
        self.sum_metric = 0.0

    def update(self, labels, preds):
        for label, pred in zip(labels, preds):
            pred = nd.argmax(pred, axis=self.axis)
            check_shapes(label, pred)
            pred = pred.asnumpy().astype('int32')
            label = label.asnumpy().astype('int32')
            label = label.flat
            pred = pred.flat

            self.sum_metric += (pred == label).sum()
            self.num_inst += 1


# mean Pixel Accuracy = (\sum_c^K TP_c / (P = TP_c + FN_c))，这里与FuseNet里面不同，但我感觉应该是这样的
class meanPixelAccuracy(mx.metric.EvalMetric):
    def __init__(self, num_classes, name = 'mpa', axis=0, **kwargs):
        super(meanPixelAccuracy, self).__init__(name, **kwargs)
        self.axis = axis
        self.num_classes = num_classes
        self.sum_metric = 0.0

    def update(self, labels, preds):
        for label, pred in zip(labels, preds):
            pred = nd.argmax(pred, axis=self.axis)
            check_shapes(label, pred)
            if isinstance(label, nd.NDArray):
                label = label.asnumpy().astype('int32')
            if isinstance(pred, nd.NDArray):
                pred = pred.asnumpy().astype('int32')
            pred_mask = extract_mask(pred, self.num_classes)
            label_mask = extract_mask(label, self.num_classes)
            acc = list([0]) * self.num_classes
            for i, c in enumerate(range(self.num_classes)):
                curr_pred_mask = pred_mask[i, :, :]
                curr_label_mask = label_mask[i, :, :]
                n_ii = np.sum(curr_label_mask == curr_pred_mask)
                t_i = np.sum(curr_label_mask)
                if (t_i != 0):
                    acc[i] = n_ii / t_i
                else:
                    acc[i] = 0
            self.sum_metric += np.mean(acc)
            self.num_inst += 1


# TODO: add frequency weighted (f.w.) IoU
# fwIoU = t_{ii} / (W*H) * {\sum_{i} IoU_i}
class fwIoU(mx.metric.EvalMetric):
    def __init__(self, num_classes, name='fwIoU', axis=0, **kwargs):
        super(fwIoU, self).__init__(name)
        pass

    def update(self, labels, preds):
        # To Be Complemented
        pass


def evaluate_net(num_classes, test_data, net, ctx=mx.cpu()):
    test_miou = mIoU(num_classes)
    test_pa = PixelAccuracy(num_classes)
    test_mpa = meanPixelAccuracy(num_classes)
    test_miou.reset()
    test_pa.reset()
    test_mpa.reset()
    for databatch in test_data:
        data = databatch.data[0]
        label = databatch.label[0]
        data = data.copyto(ctx)
        pred = net(data)
        pred = pred.copyto(mx.cpu())
        pred = nd.argmax(pred[0], axis=1)   # Only the original size data is updated
        test_miou.update([label], [pred])
        test_pa.update([label], [pred])
        test_mpa.update([label], [pred])
    return test_miou, test_pa, test_mpa


# 训练的时候，可以通过增加RGB图像的增广来实现模型对Depth的依赖，间接学习RGB与Depth之间的对应关系，如
#  RGB的随机裁剪以及变形等
#  RGB的亮度变化，此时可以认为学习到了Depth对应RGB亮度之间的鲁棒性!
def train(net, trainer, train_data, test_data, epoches, loss=loss_inst, num_classes=13, batch_size=2, periods=10, ctx=mx.gpu()):
    print('Start training on ', ctx)
    '''
    if isinstance(ctx, mx.Context):
        ctx = [ctx]
    '''
    for epoch in range(epoches):
        if isinstance(train_data, mx.io.DataIter):
            train_data.reset()
        for i, batch in enumerate(train_data):
            data = batch.data[0]
            label = batch.label[0]
            data = data.copyto(ctx)
            label = label.copyto(ctx)
            img = data[:, :3, :, :]
            dep = data[:, 3:, :, :]
            with autograd.record():
                pred, pred_4, pred_3, pred_2, pred_1 = net(img, dep)
                with autograd.pause():
                    label, label_4, label_3, label_2, label_1 = generate_target(label)
                loss1 = loss(pred, label)
                loss2 = loss(pred_4, label_4)
                loss3 = loss(pred_3, label_3)
                loss4 = loss(pred_2, label_2)
                loss5 = loss(pred_1, label_1)
                loss_total = loss1 + loss2 + loss3 + loss4 + loss5
            loss_total.backward()
            trainer.step(batch_size)
            if i % periods == 0:
                #print('type of loss_total: ', type(loss_total[0]))
                #print('shape of loss_total: ', loss_total.shape)
                print('Batch %d, Current loss: %.4f'%(i, nd.mean(loss_total)))
                #print('Batch %d, Current loss: %.4f'%(i, loss_total[0].asscalar()))

        if test_data is not None:
            miou, pa, mpa = evaluate_net(num_classes, test_data, net, ctx)
            print('Epoch %3d. test %s %.4f, %s %.4f, %s %.4f'%(epoch, *miou.get(), *pa.get(), *mpa.get())) # get() return two list, name + values
        else:
            print('Epoch %3d. '%(epoch))

