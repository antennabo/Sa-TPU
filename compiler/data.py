from dataclasses import dataclass, field


@dataclass
class BackendData:
    weights:     list = field(default_factory=list)  # [(W, b, scale), ...] 按图顺序，compile 前填
    instr_queue: list = field(default_factory=list)  # tile-level 指令（全层拼接），compile 后填
    programs:    dict = field(default_factory=dict)  # {layer 名: 该层指令列表}，compile 后填
    activations: list = field(default_factory=list)  # 各层中间激活，forward pass 后填
    input_data:  object = None                       # 原始输入，x_np
