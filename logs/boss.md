# 总监督日志 (boss)

监控对象：主会话抓帧 + 5 个子代理产出 + 系统进程
判定阈值：帧 >60s 无更新 = 抓帧断；产出文件 >15min 未动 = 停滞；帧画面无变化 = 疑似卡死

---
01:28  帧=01:26:12(1s前,177帧/90s~2fps)  子代理产出=none(timeline2/announce/digits/review_wolf均未创建)  进程=python4(rec2×2存活,rec2_status running saved=989/err0)/游戏=NRC-Win64-Shipping 30920 在线响应正常 判定=正常(主链路活,子代理无产出)
01:34  帧=01:33:41(0s前,1909帧已存/err0)  子代理产出=none(仍在探测期:PID95436解码测速+PID95872 probe_bbox.py 于01:33启动)  进程=python6(rec2×2+frames×2+2个探测)/游戏=alive 30920 mem1984MB响应正常  帧对比=1398000→1392533字节(有变化,未卡死)  判定=正常
