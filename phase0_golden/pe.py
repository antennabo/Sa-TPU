import numpy as np

class PE:
    def __init__(self):
        self.weight = np.int8(0)
        self.partial_sum = np.int32(0)

    def load_weight(self, weight):
        self.weight = np.int8(weight)

    def mac(self, activation):
        # to avoid overflow, we convert to int32 then do multiplication
        self.partial_sum += np.int32(self.weight) * np.int32(activation) 

    def reset(self):        
        self.partial_sum = np.int32(0)

if __name__ == "__main__":
    pe = PE()
    pe.load_weight(127)
    pe.mac(127)
    print(pe.partial_sum)
    pe.mac(-128)
    print(pe.partial_sum)
    pe.reset()
    print(pe.partial_sum)