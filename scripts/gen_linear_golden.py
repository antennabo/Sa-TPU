"""生成 SimpleCNN 里 conv + Linear 层的 satpu_top_tb golden.

SimpleCNN 结构 (见 model/SimpleCNN.py):
  feature.0 = Conv2d(1, 8, kernel=3, padding=1)   ← conv0
  feature.1 = ReLU
  feature.2 = MaxPool2d(2)
  fc.0 = Flatten
  fc.1 = Linear(1568, 64)                          ← fc1  (layer2)
  fc.2 = ReLU
  fc.3 = Linear(64, 10)                            ← fc3  (layer3, 输出)

跑法:
  python -m scripts.gen_linear_golden               # 默认 chained (端到端串联) batch=1
  python -m scripts.gen_linear_golden --isolated    # 每层独立采样, 不串
  python -m scripts.gen_linear_golden --batch 8     # batch=8 张 MNIST 图

产物: build/satpu_top_cosim/{conv0, fc1, fc3}/{A, B, bias, Y_int32, Y_int8, params}.txt
末尾自动跑 fp32 forward 与 golden fc.3 Y_int32 的 argmax 对拍。

契约见 doc/decisions.md D10 (per-tensor symmetric qint8, zp=0)。
"""

import os
import sys
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "model"))

import numpy as np
import torch
from torchvision import datasets, transforms
from torch.utils.data import DataLoader
from model.SimpleCNN import SimpleCNN

from simulator.functional.tb_stimulus import dump_linear_layer, dump_conv_layer


# (kind, layer_key_in_model, out_dir_name, post_ops_dict)
LAYERS = [
    ("conv",   "feature.0", "conv0", {"relu": True, "pool": {"kernel": 2, "stride": 2}}),
    ("linear", "fc.1",      "fc1",   {"relu": True}),
    ("linear", "fc.3",      "fc3",   {}),
]


def _wfifo_capacity_check(K_pad, N_pad, AC=8, wfifo_depth=1024):
    per_col_bytes = K_pad * (N_pad // AC)
    return per_col_bytes <= wfifo_depth, per_col_bytes


def _reshape_prev_for_next(prev_out, next_layer):
    """把上一层的 int8 输出 reshape 成 next_layer 期望的 A 形状。

    - next Linear: prev 输出摊平成 (batch, in_features), CHW ordering 与 Flatten 一致
    - next Conv2d: prev 输出保持 (N, C, H, W) NCHW
    """
    if isinstance(next_layer, torch.nn.Linear):
        # prev_out 可能是 (N, K, H, W)  或者已是 (batch, features)
        N = prev_out.shape[0]
        return prev_out.reshape(N, -1)
    if isinstance(next_layer, torch.nn.Conv2d):
        assert prev_out.ndim == 4, f"Conv 输入需 4D, 收到 shape={prev_out.shape}"
        return prev_out
    raise TypeError(f"unknown next layer type: {type(next_layer).__name__}")


def _run_layer(model, sample_x, calibration_loader,
               kind, layer_key, out_dir_name, post_ops,
               *, input_int8_override=None, scale_x_override=None):
    out_dir = os.path.join(ROOT, "build/satpu_top_cosim", out_dir_name)
    post_relu = post_ops.get("relu", False)
    post_pool = post_ops.get("pool", None)
    common = dict(
        calibration_loader=calibration_loader,
        post_relu=post_relu,
        input_int8_override=input_int8_override,
        scale_x_override=scale_x_override,
    )
    if kind == "linear":
        result = dump_linear_layer(model, sample_x, layer_key, out_dir, **common)
    elif kind == "conv":
        result = dump_conv_layer(model, sample_x, layer_key, out_dir,
                                 post_pool=post_pool, **common)
    else:
        raise ValueError(f"unknown kind={kind}")

    K_pad = result["B"].shape[0]
    N_pad = result["B"].shape[1]
    fits, per_col = _wfifo_capacity_check(K_pad, N_pad)

    post_ops_str = "+".join(k for k in ("relu", "pool") if post_ops.get(k))
    chain_tag = " (chained)" if input_int8_override is not None else ""
    print(f"[gen_linear_golden] {kind:6s} layer={layer_key}"
          f"{' (+' + post_ops_str + ')' if post_ops_str else ''}{chain_tag} → {out_dir}")
    print(f"  A shape:       {result['A'].shape} (int8)")
    print(f"  B shape:       {result['B'].shape} (int8)")
    print(f"  bias shape:    {result['bias'].shape} (int32)")
    print(f"  Y_int32 shape: {result['Y_int32'].shape}  "
          f"sample row 0: {result['Y_int32'][0, :4]}")
    print(f"  Y_int8  shape: {result['Y_int8'].shape}  "
          f"sample row 0: {result['Y_int8'][0, :4]}")
    if "Y_int8_pool" in result:
        print(f"  Y_int8_pool:   {result['Y_int8_pool'].shape}  "
              f"sample row 0: {result['Y_int8_pool'][0, :4]}")
    print(f"  scales: scale_x={result['scale_x']:.4e}  "
          f"scale_w={result['scale_w']:.4e}  scale_y={result['scale_y']:.4e}")
    print(f"  M_float={result['M_float']:.4e}  M0={result['M0']}  shift={result['shift']}")
    print(f"  wfifo/col: {per_col} int8 slots  (depth=1024)  "
          f"{'✓ fits' if fits else '✗ EXCEEDS — tb 需切 K/N 多 pass 累加'}")
    return result


def _extract_next_layer_input(result, this_layer, batch_size, next_layer):
    """从本层 result 里取出下一层 A 需要的 int8 张量。

    - 有 pool: 用 post-pool 的 NCHW 张量 (Y_int8_pool_nchw)
    - Linear 输出: 取 Y_int8 有效区 [:batch, :out_features]
    """
    if "Y_int8_pool_nchw" in result:
        y = result["Y_int8_pool_nchw"]                      # (N, K, Hp, Wp) int8
    elif isinstance(this_layer, torch.nn.Linear):
        y = result["Y_int8"][:batch_size, :this_layer.out_features]
    else:
        # Conv 无 pool: 需要把 (M, N_pad) 有效区 reshape 回 NCHW
        raise NotImplementedError("conv 无 pool 的下游 reshape 暂未实现 (SimpleCNN 场景不出现)")
    return _reshape_prev_for_next(y, next_layer)


def _verify_argmax(model, sample_x, fc3_result, batch_size,
                   *, labels=None, out_features=10, per_sample_threshold=16):
    """跑 fp32 forward, 与 golden fc.3 Y_int32 的 argmax 逐样本对拍。

    Y_int32 与 fp32 输出成正比 (差常数系数 1/(scale_x*scale_w)), 所以 argmax 应完全一致。
    Softmax 是单调函数, argmax 不受影响 → 不用显式算 softmax。
    labels: (batch,) 可选 ground-truth 标签。给了就报 fp32/golden 的 accuracy + 量化 loss。
    per_sample_threshold: batch 超过它就不逐样本打印, 只出汇总。
    """
    with torch.no_grad():
        fp32_out = model(sample_x)                          # (batch, out_features)
    fp32_pred = fp32_out.argmax(dim=-1).numpy()

    golden_logits = fc3_result["Y_int32"][:batch_size, :out_features]   # (batch, 10)
    golden_pred = golden_logits.argmax(axis=-1)

    n_agree = int(np.sum(fp32_pred == golden_pred))
    print(f"\n[verify] fp32 vs golden argmax  (batch_size={batch_size})")

    if batch_size <= per_sample_threshold:
        for i in range(batch_size):
            marker = "✓" if fp32_pred[i] == golden_pred[i] else "✗ MISMATCH"
            line = (f"  sample[{i}]: fp32={fp32_pred[i]}  golden={golden_pred[i]}  {marker}")
            if labels is not None:
                lab = int(labels[i])
                mf = "✓" if fp32_pred[i] == lab else "✗"
                mg = "✓" if golden_pred[i] == lab else "✗"
                line += f"   [label={lab} fp32{mf} golden{mg}]"
            print(line)

    agree_pct = 100.0 * n_agree / batch_size
    print(f"[verify] fp32 ↔ golden agree: {n_agree}/{batch_size}  ({agree_pct:.2f}%)")

    if labels is not None:
        labels_np = labels.numpy() if hasattr(labels, "numpy") else np.asarray(labels)
        fp32_acc = float(np.mean(fp32_pred == labels_np))
        gold_acc = float(np.mean(golden_pred == labels_np))
        loss_pp = 100.0 * (fp32_acc - gold_acc)  # 百分点
        print(f"[verify] fp32   accuracy:            {100*fp32_acc:6.2f}%")
        print(f"[verify] golden accuracy:            {100*gold_acc:6.2f}%")
        print(f"[verify] quantization loss (Δacc):   {loss_pp:+6.2f} pp")

        # 分歧样本一览: 先看 fp32→golden 差的地方, 再看两个都错但错得不一样的
        disagree_idx = np.where(fp32_pred != golden_pred)[0]
        if len(disagree_idx) > 0:
            print(f"[verify] {len(disagree_idx)} 张分歧样本, 前 5:")
            for i in disagree_idx[:5]:
                lab = int(labels_np[i])
                mf = "✓" if fp32_pred[i] == lab else "✗"
                mg = "✓" if golden_pred[i] == lab else "✗"
                print(f"    sample[{i}]: label={lab}  "
                      f"fp32={fp32_pred[i]}({mf})  golden={golden_pred[i]}({mg})")
    return n_agree == batch_size


def _build_sample(dataset, batch_size):
    """取前 batch_size 张 MNIST 图, 堆成 (N, 1, 28, 28) tensor 和 (N,) label tensor。"""
    xs = torch.stack([dataset[i][0] for i in range(batch_size)])
    ys = torch.tensor([dataset[i][1] for i in range(batch_size)])
    return xs, ys


def main(chained=True, batch_size=1):
    model = SimpleCNN()
    model.load_state_dict(torch.load(os.path.join(ROOT, "model/simple_cnn.pth"),
                                     map_location="cpu"))
    model.eval()

    dataset = datasets.MNIST(os.path.join(ROOT, "build/data"),
                             train=False, download=False,
                             transform=transforms.ToTensor())
    calibration_loader = DataLoader(dataset, batch_size=32, shuffle=False)
    sample_x, labels = _build_sample(dataset, batch_size)

    prev_input_int8 = None
    prev_scale_y = None
    fc3_result = None
    for idx, (kind, layer_key, out_name, post_ops) in enumerate(LAYERS):
        result = _run_layer(
            model, sample_x, calibration_loader,
            kind, layer_key, out_name, post_ops,
            input_int8_override=prev_input_int8 if chained else None,
            scale_x_override=prev_scale_y if chained else None,
        )
        if idx == len(LAYERS) - 1:
            fc3_result = result
        # 为下一层准备 int8 A + scale_x_override
        if chained and idx + 1 < len(LAYERS):
            _, next_key, _, _ = LAYERS[idx + 1]
            this_layer = model.get_submodule(layer_key)
            next_layer = model.get_submodule(next_key)
            prev_input_int8 = _extract_next_layer_input(
                result, this_layer, batch_size, next_layer)
            prev_scale_y = result["scale_y"]
        print()

    _verify_argmax(model, sample_x, fc3_result, batch_size, labels=labels)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--isolated", action="store_true",
                        help="每层独立采样, 不串上一层输出")
    parser.add_argument("--batch", type=int, default=1,
                        help="MNIST 图片数, 默认 1")
    args = parser.parse_args()
    main(chained=not args.isolated, batch_size=args.batch)
