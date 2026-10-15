# WiseNET validation (real building, ground truth)

Model: yolo11s_openvino_model on Intel GPU (OpenVINO). Sets: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11. Internal use only (CC BY-NC-SA 4.0).

## Room headcount accuracy (per second, time-weighted across sets)

| Room | Method | Seconds | Exact | Within ±1 | Bias (people) |
|---|---|---|---|---|---|
| zone_corridor | twin_primary_camera | 814 | 80.1% | 93.0% | -0.40 |
| zone_workroom | twin_primary_camera | 814 | 50.6% | 76.7% | -0.63 |
| zone_vision_lab | twin_primary_camera | 814 | 87.6% | 96.3% | -0.03 |
| zone_print_room | twin_primary_camera | 280 | 100.0% | 100.0% | +0.00 |
| zone_corridor | fused_max_over_cameras | 803 | 87.6% | 93.7% | -0.21 |
| zone_workroom | fused_max_over_cameras | 803 | 49.5% | 82.1% | -0.41 |
| zone_vision_lab | fused_max_over_cameras | 803 | 87.8% | 96.8% | -0.06 |
| zone_print_room | fused_max_over_cameras | 273 | 100.0% | 100.0% | +0.00 |

## Person detection vs hand-drawn boxes (all sets)

| Camera | GT boxes | Precision | Recall |
|---|---|---|---|
| cam_1 | 770 | 0.79 | 0.74 |
| cam_2 | 3255 | 0.95 | 0.78 |
| cam_3 | 2119 | 0.86 | 0.78 |
| cam_4 | 8262 | 0.83 | 0.71 |
| cam_5 | 6319 | 0.81 | 0.70 |
