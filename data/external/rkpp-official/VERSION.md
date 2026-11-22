# rkpp-official 官方数据导出

来源：洛克PVP助手（GitHub HZ-KMNO/roco-sandbox 衍生）的 rkpp_data.sqlite，
其数据由游戏客户端 PAK 解包（`roco-pak-unpack`），版权属《洛克王国：世界》IP 方。
项目本体 MIT 许可；本目录仅引用**数据**，不复制其代码。

生成时间：2026-11-22 10:25:56
源库：C:/Users/Lenovo/Downloads/洛克PVP助手/洛克PVP助手/app/rkpp/rkpp_data.sqlite

| 文件 | 内容 | 数量 |
|---|---|---|
| skills_official.json | 官方技能表（skill_priority 真值 / 伤害类型 / 效果） | 1655 |
| skillbanks.json | 精灵 -> 等级 -> 可学技能池 | 16828 |
| buff_map_official.json | buff / effect 名称映射 | 2164 |

更新方式：重跑 `scripts/export_rkpp_official.py`。
