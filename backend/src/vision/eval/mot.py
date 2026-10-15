"""Pure MOT-style evaluation helpers (numpy only, no I/O).

Boxes are (x1, y1, x2, y2) in pixels. Matching is greedy on IoU, highest first, which is a
simplification of the Hungarian matching used by official MOT tooling; the numbers are for
internal comparison between our own configurations, not for leaderboard claims.
"""
from typing import Dict, List, Sequence, Tuple

import numpy as np

Box = Tuple[float, float, float, float]
# (object_id, box)
Labeled = Tuple[int, Box]


def iou_matrix(a: Sequence[Box], b: Sequence[Box]) -> np.ndarray:
    """IoU between every box in a (rows) and every box in b (cols)."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=float)
    A = np.asarray(a, dtype=float)[:, None, :]
    B = np.asarray(b, dtype=float)[None, :, :]
    ix1 = np.maximum(A[..., 0], B[..., 0])
    iy1 = np.maximum(A[..., 1], B[..., 1])
    ix2 = np.minimum(A[..., 2], B[..., 2])
    iy2 = np.minimum(A[..., 3], B[..., 3])
    inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
    area_a = (A[..., 2] - A[..., 0]) * (A[..., 3] - A[..., 1])
    area_b = (B[..., 2] - B[..., 0]) * (B[..., 3] - B[..., 1])
    union = area_a + area_b - inter
    return np.where(union > 0, inter / np.where(union > 0, union, 1.0), 0.0)


def greedy_match(iou: np.ndarray, threshold: float = 0.5) -> List[Tuple[int, int]]:
    """Greedy one-to-one matching: repeatedly take the highest remaining IoU >= threshold."""
    pairs: List[Tuple[int, int]] = []
    if iou.size == 0:
        return pairs
    candidates = [(iou[i, j], i, j) for i in range(iou.shape[0]) for j in range(iou.shape[1]) if iou[i, j] >= threshold]
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    used_rows, used_cols = set(), set()
    for _, i, j in candidates:
        if i in used_rows or j in used_cols:
            continue
        used_rows.add(i)
        used_cols.add(j)
        pairs.append((i, j))
    return pairs


def evaluate_sequence(
    gt_by_frame: Dict[int, List[Labeled]],
    pred_by_frame: Dict[int, List[Labeled]],
    iou_threshold: float = 0.5,
) -> Dict[str, float]:
    """Detection precision/recall and ID switches over the frames present in gt_by_frame.

    An ID switch is counted each time a ground-truth object is matched to a different predicted
    track ID than at its previous match.
    """
    tp = gt_total = pred_total = id_switches = 0
    last_pred_for_gt: Dict[int, int] = {}
    for frame in sorted(gt_by_frame):
        gt = gt_by_frame.get(frame, [])
        pred = pred_by_frame.get(frame, [])
        gt_total += len(gt)
        pred_total += len(pred)
        pairs = greedy_match(iou_matrix([g[1] for g in gt], [p[1] for p in pred]), iou_threshold)
        tp += len(pairs)
        for gi, pi in pairs:
            gt_id, pred_id = gt[gi][0], pred[pi][0]
            prev = last_pred_for_gt.get(gt_id)
            if prev is not None and prev != pred_id:
                id_switches += 1
            last_pred_for_gt[gt_id] = pred_id
    return {
        "frames": len(gt_by_frame),
        "gt_boxes": gt_total,
        "pred_boxes": pred_total,
        "true_positives": tp,
        "precision": round(tp / pred_total, 4) if pred_total else 0.0,
        "recall": round(tp / gt_total, 4) if gt_total else 0.0,
        "id_switches": id_switches,
    }


def parse_mot_gt(text: str) -> Dict[int, List[Labeled]]:
    """Parse MOT17 gt.txt: frame,id,x,y,w,h,conf,class,visibility. Keeps conf==1 and class==1 (pedestrian)."""
    out: Dict[int, List[Labeled]] = {}
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 8:
            continue
        frame, oid = int(float(parts[0])), int(float(parts[1]))
        x, y, w, h = (float(v) for v in parts[2:6])
        conf, cls = int(float(parts[6])), int(float(parts[7]))
        if conf != 1 or cls != 1:
            continue
        out.setdefault(frame, []).append((oid, (x, y, x + w, y + h)))
    return out
