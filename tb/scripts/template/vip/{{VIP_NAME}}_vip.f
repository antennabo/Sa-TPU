// {{VIP_NAME}}_vip filelist
// compile order: interface first (module), then package (contains all classes)

// interface - must be outside package
agent/{{VIP_NAME}}_if.sv

// package - contains all UVM classes
{{VIP_NAME}}_pkg.sv
