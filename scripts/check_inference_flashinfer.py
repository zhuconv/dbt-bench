#!/usr/bin/env python3
"""Build/load the pinned model's actual FlashInfer GDN extension before serving."""
import hashlib
import json
import os
from pathlib import Path

if __name__ == '__main__':
    from flashinfer.gdn_prefill import get_gdn_prefill_module
    from flashinfer.jit.cpp_ext import get_cuda_path
    from flashinfer.jit import env
    assert get_cuda_path() == os.environ['CUDA_HOME']
    print(json.dumps({'stage':'building', 'cuda_home':get_cuda_path(),
                      'jit_dir':str(env.FLASHINFER_JIT_DIR)}), flush=True)
    module = get_gdn_prefill_module()
    ninja = env.FLASHINFER_JIT_DIR/'gdn_prefill_sm90/build.ninja'
    library = env.FLASHINFER_JIT_DIR/'gdn_prefill_sm90/gdn_prefill_sm90.so'
    assert ('cuda_home = '+get_cuda_path()) in ninja.read_text().splitlines()
    print(json.dumps({'passed':True, 'scope':'Actual FlashInfer GDN compile/link/load; model execution checked separately',
                      'files':[{'path':str(p), 'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                               for p in (ninja, library)]}), flush=True)
