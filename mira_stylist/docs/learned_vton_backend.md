# Learned VTON Backend

`mira_stylist` now supports two learned-backend integration paths for try-on preview generation:

1. External runner mode
2. IDM-VTON local checkout adapter mode
3. Built-in local diffusers runner mode

## External Runner Mode

Set:

```bash
export MIRA_STYLIST_VTON_RUNNER="python3 path/to/runner.py {request_json} {output_dir}"
```

Use this when:

- you have an official VTON repository checkout
- you have a custom inference service
- you want full control over preprocessing, device handling, and checkpoint loading

The request payload includes:

- avatar image path
- garment image path
- person segmentation metadata path
- person segmentation mask path
- pose metadata path
- garment category and color

## Built-in Local Diffusers Runner

If `MIRA_STYLIST_VTON_RUNNER` is unset and `MIRA_STYLIST_VTON_MODEL_PATH` is set, `mira_stylist` automatically uses the built-in learned runner at `mira_stylist/tools/vton_diffusers_runner.py`.

Required environment:

```bash
export MIRA_STYLIST_VTON_MODEL_PATH=/absolute/path/to/local/diffusers-inpaint-model
```

Optional tuning:

```bash
export MIRA_STYLIST_VTON_DEVICE=auto
export MIRA_STYLIST_VTON_DTYPE=float32
export MIRA_STYLIST_VTON_STEPS=24
export MIRA_STYLIST_VTON_GUIDANCE_SCALE=6.5
export MIRA_STYLIST_VTON_STRENGTH=0.88
```

What the built-in runner does:

- uses the avatar photo as the base image
- uses person segmentation and pose metadata to define the replacement zone
- pastes the uploaded garment image into that zone as conditioning
- runs a local diffusers inpainting pipeline to synthesize a more coherent result

What it does not do yet:

- full proprietary retailer-grade VTON
- dense pose / clothing-agnostic representation
- robust hand and hair occlusion handling
- guaranteed lower-body performance

## Recommended Next Backend

If you want closer-to-retailer quality, point the external runner mode at:

- an official IDM-VTON-style repository checkout with local checkpoints
- a dedicated GPU inference service

That should sit behind the current `VTONService` adapter so the rest of the Stylist API does not need to change.

## IDM-VTON Adapter Mode

If `MIRA_STYLIST_VTON_RUNNER` is unset and either `MIRA_STYLIST_IDM_VTON_REPO_PATH` or `MIRA_STYLIST_IDM_VTON_SERVER_URL` is set, `mira_stylist` uses `mira_stylist/tools/idm_vton_runner.py`.

Recommended environment:

```bash
export MIRA_STYLIST_IDM_VTON_REPO_PATH=/absolute/path/to/IDM-VTON
export MIRA_STYLIST_IDM_VTON_PYTHON_BIN=/absolute/path/to/idm-vton-env/bin/python
export MIRA_STYLIST_IDM_VTON_SERVER_URL=http://127.0.0.1:7860
export MIRA_STYLIST_IDM_VTON_DENOISE_STEPS=30
export MIRA_STYLIST_IDM_VTON_SEED=42
export MIRA_STYLIST_IDM_VTON_AUTO_MASK=true
export MIRA_STYLIST_IDM_VTON_AUTO_CROP=false
```

Expected local repo layout:

```text
IDM-VTON/
  inference.py
  gradio_demo/
    app.py
  ckpt/
  configs/
```

Recommended startup flow:

1. Create and activate the dedicated IDM-VTON environment from the upstream repo.
2. Download the required IDM-VTON checkpoints into the local checkout as instructed by that repo.
3. Start the local demo/API from the IDM-VTON checkout.
4. Start `mira_stylist` with the env vars above.

What the adapter does:

- reads the normalized MIRA payload
- maps garment category to IDM-VTON body-part classes
- calls the local IDM-VTON Gradio API
- stores the returned preview path as the primary front preview artifact

What it does not do:

- install IDM-VTON for you
- download checkpoints for you
- rewrite MIRA around IDM-VTON internals
