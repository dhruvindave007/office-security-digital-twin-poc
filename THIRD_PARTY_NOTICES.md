# Third-party notices and licensing

**Read this before any commercial deployment.** It lists everything this project builds on whose licence
places conditions on how you may use it.

## ⚠️ Items that restrict commercial use

| Component | Licence | What it means for you |
|---|---|---|
| **Ultralytics YOLO** (`ultralytics`, optional `[vision]` extra; detector weights `yolo11*.pt`) | **AGPL-3.0** | If you offer the system to others over a network, AGPL requires you to publish your complete source under AGPL. For proprietary or commercial deployment, either buy an [Ultralytics Enterprise licence](https://www.ultralytics.com/license) **or** replace the detector. The tracker sits behind `PersonTracker` (`backend/src/vision/tracking/`), so a permissively licensed detector such as RT-DETR (Apache-2.0) or a YOLOX/OpenVINO model can be swapped in without touching the rest of the system. The core system (twin, rules, access fusion, AI investigation, dashboard) runs **without** this package. |
| **WiseNET dataset** (footage, IFC building model) | **CC BY-NC-SA 4.0** | Non-commercial. Not included in this repository. Files *derived* from it (`data/sites/wisenet/`: floor plan image, IFC extract, calibration, validation results) and the dashboard screenshot in `docs/assets/` are for internal evaluation only. Remove `data/sites/wisenet/` from any commercial distribution. Citation: R. Marroquin et al., *WiseNET: An indoor multi-camera multi-space dataset with contextual information and annotations for people detection and tracking*, Data in Brief, 2019. |
| **MOT17 benchmark** | **CC BY-NC-SA 3.0** | Used only for internal tracker evaluation; never committed (`data/datasets/` is git-ignored). |

## Media used in development (not in the repository)

| File | Licence | Attribution |
|---|---|---|
| Pedestrian area Ljubljana | CC BY 3.0 | "Pedestrian area Ljubljana.webm" by Sounds of Change, Wikimedia Commons |
| Video Codec Test pedestrian area 1080p25 | CC0 1.0 | Taurus Media Technik, Wikimedia Commons |

## Permissive dependencies (no restriction on commercial use; keep their notices)

| Area | Packages | Licences |
|---|---|---|
| Backend | FastAPI, Starlette, Pydantic, pydantic-settings, Uvicorn, python-dotenv, PyYAML, NumPy, Shapely | MIT / BSD |
| AWS | boto3, botocore | Apache-2.0 |
| Vision acceleration | OpenVINO | Apache-2.0 |
| Dashboard | React, React DOM, Vite, Tailwind CSS, lucide-react | MIT / ISC |
| Tests | pytest, Vitest, Testing Library, Playwright | MIT / Apache-2.0 |

The full dependency list with exact versions is in `backend/pyproject.toml` and `frontend/package-lock.json`.

## Cloud AI

AI investigation calls models on **Amazon Bedrock** in your own AWS account, under your AWS agreement and the
model provider's terms. It is optional and off by default for automatic use.
