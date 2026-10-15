import numpy as np
import pytest

from src.vision.eval.mot import evaluate_sequence, greedy_match, iou_matrix, parse_mot_gt


def test_iou_matrix_hand_computed():
    a = [(0, 0, 10, 10)]
    b = [(0, 0, 10, 10), (5, 5, 15, 15), (20, 20, 30, 30)]
    m = iou_matrix(a, b)
    assert m.shape == (1, 3)
    assert m[0, 0] == pytest.approx(1.0)
    # overlap 5x5=25, union 100+100-25=175
    assert m[0, 1] == pytest.approx(25 / 175)
    assert m[0, 2] == pytest.approx(0.0)


def test_iou_matrix_empty_inputs():
    assert iou_matrix([], [(0, 0, 1, 1)]).shape == (0, 1)
    assert iou_matrix([(0, 0, 1, 1)], []).shape == (1, 0)


def test_greedy_match_takes_highest_iou_first_and_is_one_to_one():
    iou = np.array([[0.9, 0.6], [0.8, 0.55]])
    # (0,0)=0.9 first, then row1 can only take col1 (0.55)
    assert greedy_match(iou, 0.5) == [(0, 0), (1, 1)]
    assert greedy_match(iou, 0.7) == [(0, 0)]


def test_evaluate_sequence_precision_recall_and_id_switches():
    box = (0, 0, 10, 10)
    far = (100, 100, 110, 110)
    gt = {1: [(7, box)], 2: [(7, box)], 3: [(7, box)], 4: [(7, box), (8, far)]}
    # frame1 pred id 1, frame2 pred id 1, frame3 pred id 2 (switch), frame4 pred id 2 + a false positive
    pred = {1: [(1, box)], 2: [(1, box)], 3: [(2, box)], 4: [(2, box), (9, (50, 50, 60, 60))]}
    m = evaluate_sequence(gt, pred)
    assert m["frames"] == 4
    assert m["gt_boxes"] == 5
    assert m["pred_boxes"] == 5
    assert m["true_positives"] == 4
    assert m["precision"] == pytest.approx(0.8)
    assert m["recall"] == pytest.approx(0.8)
    assert m["id_switches"] == 1


def test_evaluate_sequence_missing_pred_frames_count_as_misses():
    gt = {1: [(1, (0, 0, 10, 10))], 2: [(1, (0, 0, 10, 10))]}
    m = evaluate_sequence(gt, {1: [(5, (0, 0, 10, 10))]})
    assert m["recall"] == pytest.approx(0.5)
    assert m["precision"] == pytest.approx(1.0)


def test_parse_mot_gt_filters_conf_and_class_and_converts_xywh():
    text = "\n".join([
        "1,1,10,20,30,40,1,1,0.9",    # kept: pedestrian, conf 1
        "1,2,0,0,5,5,0,1,1.0",        # dropped: conf 0 (ignore region)
        "1,3,0,0,5,5,1,3,1.0",        # dropped: class 3 (car)
        "2,1,11,21,30,40,1,1,0.8",
    ])
    gt = parse_mot_gt(text)
    assert gt == {1: [(1, (10.0, 20.0, 40.0, 60.0))], 2: [(1, (11.0, 21.0, 41.0, 61.0))]}
