import numpy as np

def quantize_int8(W):
    scale = np.max(np.abs(W)) / 127
    W_int8 = np.round(W / scale).astype(np.int8)
    W_dequant = W_int8.astype(np.float32) * scale
    return W_int8, W_dequant, scale

def np_linear(x, W, b):
    return x @ W.T + b

def np_relu(x):
    return np.maximum(0, x)

def conv2d(x, W, b, padding=1):
    N, C, H_in, W_in = x.shape
    out_ch, in_ch, kH, kW = W.shape
    x_pd = np.pad(x, ((0,0), (0,0), (padding,padding), (padding,padding)), mode='constant', constant_values=0)
    H_out = H_in + 2 * padding - kH + 1
    W_out = W_in + 2 * padding - kW + 1
    out = np.zeros((N, out_ch, H_out, W_out))
    for n in range(N):
        for c in range(out_ch):
            for i in range(H_out):
                for j in range(W_out):
                    patch = x_pd[n,:,i:i+kH,j:j+kW]
                    out[n,c,i,j] = np.sum(patch * W[c]) + b[c]
    return out

def maxpool(x, kernel=2):
    N, C, H_in, W_in = x.shape
    H_out = int(H_in / kernel)
    W_out = int(W_in / kernel)
    out = np.zeros((N, C, H_out, W_out))
    for n in range(N):
        for c in range(C):
            for h in range(H_out):
                for w in range(W_out):
                    out[n,c,h,w] = np.max(x[n, c, h*kernel:h*kernel+kernel, w*kernel:w*kernel+kernel])
    return out# pass