from compiler.frontend.modelparser import modelparser

class Frontend:
    def __init__(self, dtype="fp32", accum_dtype="fp32"):
        self.parser = modelparser(dtype=dtype, accum_dtype=accum_dtype)
        self.irs    = []

    def run(self, exported) -> list:
        self.irs = self.parse(exported)
        return self.irs

    def parse(self, exported) -> list:
        return self.parser.export(exported)

    def print_irs(self):
        print(f"=== Frontend IR ({len(self.irs)} ops) ===")
        self.parser.print_irs(self.irs)
