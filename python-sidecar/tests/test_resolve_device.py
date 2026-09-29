# -*- coding: utf-8 -*-
"""
Test de main.resolve_device: "cpu" sempre respeitado; "cuda"/"auto" só viram
"cuda" se a GPU tiver kernel compilado pra ela (não só presença de driver) -
GPU velha (ex.: GTX 750 Ti, Maxwell sm_50) passa em is_available() mas cai
pra CPU se a capacidade dela não estiver em get_arch_list().

Usa torch FALSO (unittest.mock) - não depende da GPU real desta máquina.

Rodar:  python tests/test_resolve_device.py
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from main import resolve_device, resolve_whisper_device


def _fake_torch(available: bool, capability=None, arch_list=None):
    t = MagicMock()
    t.cuda.is_available.return_value = available
    if capability is not None:
        t.cuda.get_device_capability.return_value = capability
    if arch_list is not None:
        t.cuda.get_arch_list.return_value = arch_list
    return t


def test_cpu_pedido_sempre_cpu():
    assert resolve_device("cpu") == "cpu"


def test_cuda_com_capacidade_suportada():
    fake = _fake_torch(True, capability=(8, 6), arch_list=["sm_61", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90"])
    with patch.dict(sys.modules, {"torch": fake}):
        assert resolve_device("cuda") == "cuda"
        assert resolve_device("auto") == "cuda"


def test_gpu_antiga_incompativel_cai_pra_cpu():
    # GTX 750 Ti: Maxwell, sm_50 - fora da lista de kernels do torch atual.
    fake = _fake_torch(True, capability=(5, 0), arch_list=["sm_61", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90"])
    with patch.dict(sys.modules, {"torch": fake}):
        assert resolve_device("auto") == "cpu"


def test_sem_cuda_disponivel_cai_pra_cpu():
    fake = _fake_torch(False)
    with patch.dict(sys.modules, {"torch": fake}):
        assert resolve_device("auto") == "cpu"


def test_gpu_amd_rocm_vira_cuda():
    # torch ROCm: a GPU AMD aparece como "cuda" (HIP); a lista de arquiteturas
    # é gfx* (sem sm_*), então o corte de capacidade não se aplica.
    fake = _fake_torch(True, capability=(11, 0), arch_list=["gfx1030", "gfx1100", "gfx1101"])
    with patch.dict(sys.modules, {"torch": fake}):
        assert resolve_device("auto") == "cuda"


def _fake_ct2(cuda_devices: int):
    ct2 = MagicMock()
    ct2.get_cuda_device_count.return_value = cuda_devices
    return ct2


def test_whisper_na_cpu_quando_o_resto_esta_na_cpu():
    with patch.dict(sys.modules, {"ctranslate2": _fake_ct2(1)}):
        assert resolve_whisper_device("cpu") == "cpu"


def test_whisper_na_gpu_quando_o_ctranslate2_ve_a_gpu():
    # NVIDIA (ou build ROCm do CTranslate2): nada muda
    with patch.dict(sys.modules, {"ctranslate2": _fake_ct2(1)}):
        assert resolve_whisper_device("cuda") == "cuda"


def test_whisper_na_cpu_com_torch_rocm_e_ctranslate2_so_cuda():
    # GPU AMD: torch diz "cuda", mas o CTranslate2 do PyPI não enxerga GPU
    with patch.dict(sys.modules, {"ctranslate2": _fake_ct2(0)}):
        assert resolve_whisper_device("cuda") == "cpu"


def test_whisper_na_cpu_se_o_ctranslate2_falhar():
    broken = MagicMock()
    broken.get_cuda_device_count.side_effect = RuntimeError("driver")
    with patch.dict(sys.modules, {"ctranslate2": broken}):
        assert resolve_whisper_device("cuda") == "cpu"


if __name__ == "__main__":
    test_gpu_amd_rocm_vira_cuda()
    test_whisper_na_cpu_quando_o_resto_esta_na_cpu()
    test_whisper_na_gpu_quando_o_ctranslate2_ve_a_gpu()
    test_whisper_na_cpu_com_torch_rocm_e_ctranslate2_so_cuda()
    test_whisper_na_cpu_se_o_ctranslate2_falhar()
    test_cpu_pedido_sempre_cpu()
    test_cuda_com_capacidade_suportada()
    test_gpu_antiga_incompativel_cai_pra_cpu()
    test_sem_cuda_disponivel_cai_pra_cpu()
    print("OK test_resolve_device")
