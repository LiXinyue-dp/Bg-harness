#!/usr/bin/env bash
# 上传侧机器出网代理有 ~98KB POST 体硬墙, 整文件传不上去, 故拆成 6 块。
# 还原整文件:
cat "feedback_samples_celebrity20x5_landmarks20x5.jsonl".part_* > "feedback_samples_celebrity20x5_landmarks20x5.jsonl"
md5sum "feedback_samples_celebrity20x5_landmarks20x5.jsonl"
# 期望 md5: 2c8277955940295ede6b62c78a6a707f
