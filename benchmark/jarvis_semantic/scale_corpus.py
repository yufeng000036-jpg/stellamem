"""Nested synthetic corpus. Transform only annotated source spans; never read questions to tune it."""

import hashlib
import json
from collections import Counter

SIZES = (20, 500, 2000)
COUNTS = ((0, 0, 0), (320, 100, 60), (1460, 400, 120))
PLACES = ("沙洲", "锦南", "梧桐", "溪谷", "南塘", "临浦", "云亭", "平湖", "玉泉", "秀水")
# Ordinary synthetic notes have people, actions and context; the instances remain templated.
SCENES = (
    "在{p}排练结束后，{n}把借来的谱架送回了侧门值班室。",
    "{n}问过{p}的装裱师傅，取画那天要带纸质收据。",
    "{p}的周末小组换了教室，{n}到走廊尽头才看到新门牌。",
    "上次到{p}听讲座，{n}把速记本忘在最后一排，散场才想起来。",
    "{p}店员跟{n}约好，修完台灯后先留在柜台，周末再去取。",
    "{n}试过从{p}北边进场，午后送货的推车会挡住入口。",
    "去{p}交换旧书时，{n}发现登记用的是扉页上的版本信息。",
    "{p}活动结束后，{n}把合照的样张放进了牛皮纸袋。",
    "{n}在{p}补领了寄存牌，店员把原来的编号记在纸单上。",
    "{p}的合唱排练提前结束，{n}把下周的谱子带回去校对。",
    "{n}在{p}寄回的样衣少了一枚扣子，裁缝另外装了个纸包。",
    "经过{p}花市时，{n}和摊主约好下雨也在棚子里面交盆栽。",
    "{p}这次放映有片尾交流，{n}把返程车次往后挪了一班。",
    "为给{p}的书展腾地方，{n}把旧书架的隔板竖着靠墙收好。",
    "{n}去{p}取照片多等了一会儿，店里重新裁了其中一张的白边。",
    "给{p}的小剧场送道具那天，{n}把易碎品交到灯光师手里。",
    "{n}在{p}参加交换活动时说过，希望用手作明信片换旧唱片。",
    "{p}的印刷店把试样留给了{n}，下次按这张样的纸张厚度下单。",
    "{n}到{p}归还棋盘，发现缺的棋子原来在布袋夹层里。",
    "{p}小组散会后，{n}把没决定的议题圈在记录最后一页。",
    "在{p}听完排练，{n}记下最后一首歌要从副歌前重新进拍。",
    "{n}把借的镜头还到{p}，当面核对了遮光罩和两个盖子。",
    "{p}旧货店收到一张折叠桌，{n}量过宽度才确定能搬进门。",
    "{n}想给{p}的展板换底纸，先留了一小块旧纸配颜色。",
    "{p}的小厨房这周少一把量勺，{n}借了邻桌的用完即还。",
    "在{p}排练读稿时，{n}给需要停顿的地方贴了细窄的纸签。",
    "{n}发现给{p}寄去的相框少个支脚，另装一袋随下一箱补去。",
    "{p}手作课临时改用水洗颜料，{n}把原先带的油画笔收了起来。",
    "为记住{p}的线路变化，{n}画了一张只标出换乘站的草图。",
    "{n}到{p}排队领材料才知道，领取顺序以纸单上的序号为准。",
    "{p}的摆摊名单上少了一个名字，{n}和主持人当场核对了登记页。",
    "{n}在{p}试过两只保温壶，更喜欢按盖便能倒水的那一只。",
    "{p}收回的志愿活动围裙还没干，{n}把它们分开挂在窗边。",
    "{n}帮{p}清点桌游时，把不同版本的规则卡分别装袋。",
    "{p}门厅修灯那天，{n}从侧廊过去，把签到册带到了新入口。",
    "{n}上次在{p}买的线不够长，回去时特意带了旧线头比色。",
    "约在{p}碰面的两个人都到早了，{n}顺路先把借阅袋交给前台。",
    "{p}留给{n}的座位旁边有插座，排练录音时不用再接长线。",
    "{n}在{p}教室门内发现遗漏的水壶，贴上场次标签交给值班员。",
    "{p}做活动复盘时，{n}提出下次先试走一次物料搬运路线。",
)
PEOPLE = ("小岚", "阿禾", "小顾", "阿云", "小乔", "阿牧", "小闻", "阿陶")
ITEMS = ("挂钟", "坐垫", "立牌", "杯垫", "布帘", "墙贴", "灯罩")


def fingerprint(obj):
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def group(q):
    return q.get("group", q["id"][:1])


def check_base(data, blind):
    notes, qs = data["notes"], data["questions"]
    if not notes or not qs or any(not n["id"] or not n["text"] for n in notes):
        raise ValueError("empty input")
    ids = [r["id"] for r in notes]
    if len(set(ids)) != len(ids) or len({r["text"] for r in notes}) != len(notes):
        raise ValueError("duplicate source ID/text")
    if any(n["status"] not in ("active", "superseded") for n in notes):
        raise ValueError("invalid source status")
    current = {n["id"] for n in notes if n["status"] == "active"}
    if len({q["id"] for q in qs}) != len(qs) or len({q["text"] for q in qs}) != len(qs):
        raise ValueError("duplicate question ID/text")
    for q in qs:
        if group(q) not in ("D", "E", "N") or (group(q) == "N") != (q["gold"] is None):
            raise ValueError("invalid group/gold")
        if q["gold"] is not None and q["gold"] not in current:
            raise ValueError("unavailable gold")
    if blind and (len(notes), len(current), Counter(map(group, qs))) != (
        22,
        20,
        {"D": 20, "E": 8, "N": 12},
    ):
        raise ValueError("sealed v2 count mismatch")


def near_sources(notes):
    active = [n for n in notes if n["status"] == "active"]
    return (
        [n for n in active if n.get("entity")],
        [n for n in active if n.get("attribute")],
        [n for n in notes if n["status"] == "superseded" and n.get("entity")],
    )


def distractors(notes):
    pools = near_sources(notes)
    if not all(pools):
        raise ValueError(
            "annotate entity and attribute spans in active notes and entities in old notes"
        )
    rows = []
    counts = Counter()
    for f, g, h in COUNTS[1:]:
        for kind, stop in (("F", f), ("G", g), ("H", h)):
            for i in range(counts[kind], stop):
                row = {"kind": kind, "id": f"generated-{kind}{i + 1:04}"}
                if kind == "F":
                    value = f"第{i // len(ITEMS) + 1}号分馆的{ITEMS[i % len(ITEMS)]}盘点标签为{i % 17 + 1}号。"
                elif kind == "G":
                    value = SCENES[i % len(SCENES)].format(
                        p=PLACES[(i // len(SCENES) + i) % len(PLACES)],
                        n=PEOPLE[(i // len(SCENES) + i // len(PLACES)) % len(PEOPLE)],
                    )
                else:
                    k = i % 3
                    j = i // 3
                    n = pools[k][j % len(pools[k])]
                    span = n["attribute" if k == 1 else "entity"]
                    if not span or n["text"].count(span) != 1:
                        raise ValueError(f"span must occur exactly once: {n['id']}")
                    alias = f"{PLACES[j % 10]}第{j // 10 + 1}联络站"
                    if k == 1:
                        alt = n.get("alt_attribute", "")
                        if not alt or alt == span:
                            raise ValueError(f"distinct alternate attribute required: {n['id']}")
                        replacement = (
                            alt if j < len(pools[k]) else f"第{j // len(pools[k]) + 1}组的{alt}"
                        )
                    else:
                        replacement = alias + ("分会" if k == 2 else "")
                    value = n["text"].replace(span, replacement, 1)
                    row.update(
                        origin=n["id"], transform=("entity", "attribute", "old_other_entity")[k]
                    )
                rows.append(dict(row, text=value))
            counts[kind] = stop
    all_rows = notes + rows
    if len({r["text"] for r in all_rows}) != len(all_rows) or len(
        {r["id"] for r in all_rows}
    ) != len(all_rows):
        raise ValueError("source collision in transformed corpus; stop before scoring")
    return rows


if __name__ == "__main__":
    # Offline inventory for manual inspection of transforms before the single scored run.
    import argparse
    from pathlib import Path

    cli = argparse.ArgumentParser()
    cli.add_argument("manifest", type=Path)
    cli.add_argument("new_inventory", type=Path)
    args = cli.parse_args()
    if args.new_inventory.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        cli.error("write private inventory outside the checkout")
    data = json.loads(args.manifest.read_text("utf-8-sig"))
    check_base(data, False)
    rows = distractors(data["notes"])
    with args.new_inventory.open("x", encoding="utf-8") as sink:
        for tier, limit in enumerate((0, 480, 1980), 1):
            print(
                json.dumps({"tier": tier, "hash": fingerprint(data["notes"] + rows[:limit])}),
                file=sink,
            )
        for i, row in enumerate(rows):
            print(
                json.dumps(dict(row, first_tier=2 if i < 480 else 3), ensure_ascii=False), file=sink
            )
