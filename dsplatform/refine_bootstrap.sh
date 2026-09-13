#!/bin/bash
# Bootstrap a Refine worker on a bare RunPod PyTorch pod — no registry, no image build.
# The platform starts the pod with this script (base64 in BOOTSTRAP_B64) and the env:
#   PLATFORM_BASE, REFINE_WORKER_TOKEN, HF_TOKEN, RUNPOD_API_KEY (RUNPOD_POD_ID is injected),
#   GIT_TOKEN (read access to DanceSage/dancesage-research), WORKER_REF (branch, default refine)
# A network volume mounted at /workspace keeps the 24 GB of weights between pods.
set -e
export DEBIAN_FRONTEND=noninteractive PATH=/opt/conda/bin:$PATH
apt-get update -qq && apt-get install -y -qq git ffmpeg libgl1 libglib2.0-0 libegl1 libgles2 libglvnd0 libosmesa6 >/dev/null
mkdir -p /work && cd /work
CK=/workspace/ckpt; [ -d /workspace ] || CK=/work/ckpt; mkdir -p $CK; ln -sfn $CK /work/ckpt

# our code
REF=${WORKER_REF:-refine}
git clone -q --depth 1 -b $REF https://${GIT_TOKEN:+$GIT_TOKEN@}github.com/DanceSage/dancesage-research.git /work/src
cp /work/src/refine-worker/* /work/ 2>/dev/null || true
mkdir -p /work/multi-hmr && cp /work/src/refine-worker/multi-hmr/*.py /work/multi-hmr/

# environment (once per pod; ~6 min)
conda env list | grep -q body4d || conda create -y -q -n body4d python=3.12 >/dev/null
PY=/opt/conda/envs/body4d/bin/python
$PY -c "import torch" 2>/dev/null || $PY -m pip install -q torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu118
[ -d /work/body4d ] || git clone -q --depth 1 https://github.com/gaomingqi/sam-body4d.git /work/body4d
cd /work/body4d
$PY -c "import detectron2" 2>/dev/null || $PY -m pip install -q 'git+https://github.com/facebookresearch/detectron2.git@a1ce2f9' --no-build-isolation --no-deps
$PY -c "import sam3" 2>/dev/null || $PY -m pip install -q -e models/sam3
$PY -m pip install -q -e . huggingface_hub "setuptools<80" trimesh pyrender einops roma smplx ipdb
git -C models/sam_3d_body apply /work/joints-saver.patch 2>/dev/null || true
sed -i 's|ckpt_root: .*|ckpt_root: /work/ckpt|; s|^  enable: true|  enable: false|' configs/body4d.yaml
grep -q BODY4D_LITE scripts/offline_app.py || $PY /work/lite_patch.py
[ -d /work/multi-hmr/multi-hmr ] || git clone -q --depth 1 https://github.com/naver/multi-hmr.git /work/multi-hmr/multi-hmr
sed -i 's/^os.environ\["PYOPENGL_PLATFORM"\] = "egl"/# egl set by the worker/; s/^from multi_hmr_anny.multi_hmr import Multi_HMR as ModelAnny/try:\n    from multi_hmr_anny.multi_hmr import Multi_HMR as ModelAnny\nexcept ImportError:\n    ModelAnny = None/; s/torch.load(ckpt_path, map_location=device)/torch.load(ckpt_path, map_location=device, weights_only=False)/' /work/multi-hmr/multi-hmr/demo.py

# weights (once per volume; ~10 min)
mkdir -p ~/.cache/huggingface && printf '%s' "$HF_TOKEN" > ~/.cache/huggingface/token
if [ ! -f /work/ckpt/sam3/sam3.pt ]; then
  cd /work/body4d && $PY scripts/setup.py --ckpt-root /work/ckpt || true
  $PY - <<'PY'
from huggingface_hub import hf_hub_download
hf_hub_download('facebook/sam3', 'sam3.pt', local_dir='/work/ckpt/sam3')
for f in ['model.ckpt', 'model_config.yaml', 'assets/mhr_model.pt']:
    hf_hub_download('facebook/sam-3d-body-dinov3', f, local_dir='/work/ckpt/sam-3d-body-dinov3')
PY
fi
mkdir -p /work/multi-hmr/multi-hmr/models/smplx /work/multi-hmr/multi-hmr/models/multiHMR
[ -f /work/ckpt/multiHMR_672_B.pt ] || curl -s -L -o /work/ckpt/multiHMR_672_B.pt https://download.europe.naverlabs.com/ComputerVision/MultiHMR/multiHMR_672_B.pt
ln -sf /work/ckpt/multiHMR_672_B.pt /work/multi-hmr/multi-hmr/models/multiHMR/multiHMR_672_B.pt
[ -f /work/ckpt/smplx/SMPLX_NEUTRAL.npz ] && cp -n /work/ckpt/smplx/SMPLX_NEUTRAL.npz /work/multi-hmr/multi-hmr/models/smplx/ || echo "no SMPL-X on the volume: put SMPLX_NEUTRAL.npz in $CK/smplx/ for the refined tier"
[ -f /work/ckpt/smpl_mean_params.npz ] && cp -n /work/ckpt/smpl_mean_params.npz /work/multi-hmr/multi-hmr/models/ || true

cd /work && export PYOPENGL_PLATFORM=egl PY_BODY4D=$PY PY_MHMR=$PY WORK_DIR=/work
exec $PY worker.py
