#!/usr/bin/env python3
"""Compile, link and execute a CUDA kernel with the server's explicit toolkit."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

SOURCE = r'''
#include <cuda_runtime.h>
#include <cuda.h>
#include <cstdio>
__global__ void probe(int *value) { *value = 37; }
int main() {
    int count=0, driver=0, runtime=0, answer=0;
    int *device=nullptr;
    if (cuInit(0) != CUDA_SUCCESS) return 10;
    if (cudaGetDeviceCount(&count) != cudaSuccess || count < 1) return 11;
    if (cudaDriverGetVersion(&driver) != cudaSuccess) return 12;
    if (cudaRuntimeGetVersion(&runtime) != cudaSuccess) return 13;
    if (cudaMalloc(&device, sizeof(int)) != cudaSuccess) return 14;
    probe<<<1,1>>>(device);
    if (cudaGetLastError() != cudaSuccess) return 15;
    if (cudaMemcpy(&answer, device, sizeof(int), cudaMemcpyDeviceToHost) != cudaSuccess) return 16;
    if (cudaFree(device) != cudaSuccess) return 17;
    std::printf("{\"devices\":%d,\"driver\":%d,\"runtime\":%d,\"answer\":%d}\n",
                count,driver,runtime,answer);
    return answer == 37 ? 0 : 18;
}
'''


def main():
    cuda = Path(os.environ['CUDA_HOME'])
    nvcc = cuda / 'bin/nvcc'
    runtime = cuda / 'lib64/libcudart.so'
    driver_stub = cuda / 'lib64/stubs/libcuda.so'
    for path in (nvcc, runtime, driver_stub, cuda / 'include/cuda.h'):
        if not path.is_file():
            raise RuntimeError(f'Incomplete explicit CUDA toolkit: {path}')
    version = subprocess.run([str(nvcc), '--version'], check=True, capture_output=True, text=True)
    with tempfile.TemporaryDirectory(prefix='dbt-bench-cuda-') as tmp:
        source = Path(tmp) / 'probe.cu'; source.write_text(SOURCE)
        executable = Path(tmp) / 'probe'
        command = [str(nvcc), '-ccbin', os.environ['CC'], '-arch=sm_90',
                   '--cudart=shared', str(source), '-L'+str(cuda/'lib64/stubs'), '-lcuda',
                   '-Xlinker', '-rpath', '-Xlinker', str(cuda/'lib64'), '-o', str(executable)]
        build = subprocess.run(command, check=True, capture_output=True, text=True)
        result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
        device = json.loads(result.stdout)
        assert device['answer'] == 37
        print(json.dumps({'passed': True, 'cuda_home': str(cuda), 'nvcc_version': version.stdout.strip(),
                          'files': [{'path': str(p), 'resolved': str(p.resolve()),
                                     'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                                    for p in (nvcc, runtime, driver_stub)],
                          'compile_command': command, 'compile_stderr': build.stderr,
                          'device': device,
                          'scope': 'CUDA compiler, runtime/driver linkage and real SM90 kernel; model kernels checked by protocol smoke'}))


if __name__ == '__main__':
    main()
