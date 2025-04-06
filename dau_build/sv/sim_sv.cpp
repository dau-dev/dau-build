#include <iostream>
#include <cstdlib>
#include <cassert>
#include <verilated.h>

#include "Vcam_top.h"


int main(int argc, char** argv, char** env) {
    Verilated::mkdir("logs");
    VerilatedContext contextp;
    contextp.debug(0);
    contextp.randReset(2);
    contextp.traceEverOn(true);
    contextp.commandArgs(argc, argv);

    Vcam_top top;
    top.clk = 0;

    while (!contextp.gotFinish()) {
        contextp.timeInc(1);
        top.clk = !top.clk;
        top.eval();
    }
    top.final();
#if VM_COVERAGE
    contextp.coveragep()->write("logs/coverage.dat");
#endif
    return 0;
}
