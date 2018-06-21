import mxnet as mx
from mxnet import gluon
from mxnet import nd
from mxnet import autograd
import model
import readData
from matplotlib import pyplot as plt

'''
net = model.FlowNetS()
net.initialize(init=mx.init.Xavier())

x = nd.ones((1, 6, 384, 512))
y = net(x)

print(y[0].shape)    # output: (1, 2, 96, 128)
print(y[1].shape)    # output: (1, 2, 48, 64)
print(y[2].shape)    # output: (1, 2, 24, 32)
print(y[3].shape)    # output: (1, 2, 12, 16)
'''

'''
x = nd.ones((1, 1, 3, 3))
y = nd.norm(x, 1)
print(y)
'''

'''
data, label = readData.readKITTIImages()
print('len(data) = ', len(data))
print('len(label) = ', len(label))

print(type(data[0]))
d = (data[199]).transpose((1, 2, 0))
print('d.shape = ', d.shape)

a, b = nd.split(d, num_outputs=2, axis=-1)

plt.imshow(a.asnumpy())
plt.show()
'''

batch_size = 1

kittidataset = readData.KITTIDataset(True, (360, 1240))
train_data = gluon.data.DataLoader(kittidataset, batch_size, True, last_batch='discard')

'''
print(type(train_data))
for data, label in train_data:
    print('type of data = ', type(data))
    print(data.shape)
    print(label.shape)

    imgA, imgB = nd.split(data, num_outputs=2, axis=1)
    print(imgA.shape)
    imgA = (imgA[0, :, :, :]).transpose((1, 2, 0))
    print(imgA.shape)
    imgA = ((imgA * readData.rgb_std[0:3]) + readData.rgb_mean[0:3]) * 255
    imgA = imgA.astype('uint8').asnumpy()
    plt.imshow(imgA)
    break

plt.show()
'''


