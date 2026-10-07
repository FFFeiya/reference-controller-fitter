"""Storyboard of the project video (Chinese subtitles).

Usage: python tools/video/storyboard.py WORK_DIR OUT_DIR [--preview]
--preview renders one frame per scene into OUT_DIR/preview/ instead of the full video.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from compose import Composer, Scene, FPS, srt_time, keys_from_csv, QApplication  # noqa: E402


def seq(d: Path, pattern="f*.png"):
    return sorted(str(p) for p in d.glob(pattern))


def union(*rects, pad=12):
    x0 = min(r[0] for r in rects) - pad; y0 = min(r[1] for r in rects) - pad
    x1 = max(r[0] + r[2] for r in rects) + pad; y1 = max(r[1] + r[3] for r in rects) + pad
    return (x0, y0, x1 - x0, y1 - y0)


def aspect(r, a=16 / 9):
    """Grow rect r to aspect a around its centre."""
    x, y, w, h = r
    if w / h < a:
        nw = h * a; x -= (nw - w) / 2; w = nw
    else:
        nh = w / a; y -= (nh - h) / 2; h = nh
    return (x, y, w, h)


UFULL = (8, 31, 1920, 986)   # editor window without title bar / borders
GFULL = (8, 31, 1920, 1009)


def build(work: Path):
    tool = work / "tool"
    meta = json.loads((tool / "segments.json").read_text())
    S = []
    # ------------------------------------------------------------------ intro
    S.append(Scene("card", 6, title="Reference Controller Fitter",
                   lines=["从游戏录像中，自动拟合出一个可以直接玩的 2D 角色控制器",
                          "录像 → 跟踪 → 自动拟合 → 试玩 → 导出到 Unity / Godot"]))
    t3 = meta["t3_fit"]; r3 = t3["rois"]
    f3 = seq(tool / "t3_fit")
    done = t3["marks"]["done"]
    S.append(Scene("frames", frames=f3[done:done + 200], crop_from=union(r3["ref"], r3["proto"], pad=-2),
                   caption="左边是开源游戏 SuperTux 的录像，右边的白色方块是工具自动拟合出的控制器：\n"
                           "同样的按键时机下，两条跳跃弧线几乎重合。"))
    S.append(Scene("diagram", 11, title="它在做什么？",
                   lines=[("游戏录像", "一段或几段 gameplay"), ("跟踪", "找到角色，扣除镜头移动"),
                          ("推断按键", "什么时候按左右、跳跃"), ("联合拟合", "所有片段共用一套参数"),
                          ("模型选择", "能解释数据的最简单模型"), ("试玩 / 导出", "Unity C# · Godot 脚本")],
                   frames=["它不是在还原原游戏的源代码，", "而是找一个“行为相似、结构简单、参数可读、能直接运行”的控制器。"]))
    # ------------------------------------------------------------------ ① import
    ch = "① 导入录像"
    t1 = meta["t1_clip"]; r1 = t1["rois"]; f1 = seq(tool / "t1_clip")
    cx, cy, cw, chh = r1["canvas"]
    s = min(cw / 1280, chh / 720); ox = cx + (cw - 1280 * s) / 2; oy = cy + (chh - 720 * s) / 2
    tux = (ox + 94 * s, oy + 426 * s, 36 * s, 38 * s)
    around_tux = aspect((tux[0] - 120, tux[1] - 150, 520, 260))
    canvas_r = aspect(r1["canvas"])
    S.append(Scene("still", 6, chapter=ch, frames=[f1[0]],
                   caption="第一步：Add clip，选择一段游戏录像。\n这里用的是 SuperTux 的公开录像（GPL，来自 Wikimedia Commons）。"))
    S.append(Scene("frames", chapter=ch, frames=f1[:t1["marks"]["track"]], crop_from=canvas_r, crop_to=around_tux,
                   zoom_start=1.2, zoom_at=2.2,
                   caption="设定片段的起止帧，然后在第一帧用鼠标框出角色。"))
    S.append(Scene("frames", chapter=ch, frames=f1[t1["marks"]["track"]:t1["marks"]["tracked"]],
                   highlights=[(*r1["status"], 0.3)], dur=7, caption_top=True,
                   caption="点击 Track：逐帧跟踪角色，同时根据背景估计镜头的移动，\n把屏幕上的位置换算成游戏世界里的真实运动轨迹。（快进）"))
    S.append(Scene("frames", chapter=ch, frames=f1[t1["marks"]["tracked"]:], crop_from=canvas_r,
                   caption="橙色线就是角色脚底在游戏世界里的运动轨迹，镜头滚动已经被扣除。"))
    # ------------------------------------------------------------------ ② fit
    ch = "② 自动拟合"
    t2 = meta["t2_main"]; r2 = t2["rois"]; f2 = seq(tool / "t2_main")
    tl = t2["marks"]["timeline"]
    S.append(Scene("frames", chapter=ch, frames=f2[:tl], highlights=[(*r2["clips"], 0.5)],
                   caption="可以导入多段片段：起跑、跳跃、落到低处、停下……\n这里是两位玩家录制的 4 段片段，它们共用同一个控制器。"))
    S.append(Scene("frames", chapter=ch, frames=f2[tl:], crop_from=aspect(r2["timeline"], 16 / 7.5),
                   caption="时间轴上是自动推断出的按键：绿色=按住右，蓝色=左，橙色=跳跃。\n"
                           "橙点是录像里的轨迹，青线是控制器的轨迹。推断错了可以直接拖动修改。"))
    fit = t3["marks"]["fit"]
    S.append(Scene("frames", chapter=ch, frames=f3[:fit], highlights=[(*r3["proto"], 0.6), (*r3["error"], 0.6)],
                   caption="先换成一个通用的初始控制器：跳得太低、节奏也不对，误差约 47%。"))
    S.append(Scene("frames", chapter=ch, frames=f3[fit:done], highlights=[(*r3["error"], 0.2), (*r3["params"], 0.2)],
                   caption="点击 Fit Controller：工具在 A–E 五类由简到繁的模型中搜索参数，\n参数在动、误差一路下降。（快进）"))
    S.append(Scene("frames", chapter=ch, frames=f3[done:done + 210], crop_from=aspect(union(r3["error"], r3["report"])),
                   caption="最后选出能解释全部片段的最简单模型 D，轨迹误差约 2%。\n报告会如实写出：哪些参数录像确定不了、哪两种机制分不清。"))
    t4 = meta["t4_try"]; r4 = t4["rois"]; f4 = seq(tool / "t4_try")
    keys = [(bool(a), bool(d), bool(j)) for a, d, j in t4["keys"]]
    S.append(Scene("frames", chapter=ch, frames=f4, crop_from=tuple(r4["proto"]), keys=keys,
                   keys_note="按键由演示脚本发出",
                   caption="Try it：直接用键盘试玩拟合出的手感。\n轻点空格是矮跳，长按是高跳，空中转向比较“重”。"))
    f5 = seq(tool / "t5_export")
    S.append(Scene("frames", chapter=ch, frames=f5,
                   caption="Export：填入你的角色在引擎里有多高，\n导出 Unity C# 脚本 + 参数 JSON，或者 Godot 4 脚本。"))
    # ------------------------------------------------------------------ ③ Unity
    ch = "③ Unity 实操"
    us = work / "unity_shots"
    S.append(Scene("card", 3.5, chapter=ch, title="把控制器放进 Unity 6",
                   lines=["以下编辑器操作由脚本自动完成，效果与手动点击相同"]))
    S.append(Scene("still", 5, chapter=ch, frames=[str(us / "0_empty.png")], crop_from=UFULL,
                   caption="在 Unity 6 里新建一个 2D 项目。"))
    S.append(Scene("still", 8, chapter=ch, frames=[str(us / "1_imported.png")], crop_from=UFULL,
                   highlights=[(250, 750, 280, 90, 0.5), (1336, 380, 588, 590, 1.6)],
                   caption="把导出的 FittedPlatformerController.cs 和 fitted_controller.json 拖进 Assets。\n"
                           "打开脚本就能看到：拟合出的参数就写在开头。"))
    S.append(Scene("still", 6, chapter=ch, frames=[str(us / "2_level.png")], crop_from=UFULL,
                   highlights=[(4, 160, 432, 150, 0.5), (440, 112, 890, 560, 1.2)],
                   caption="搭一个简单关卡：地面、两级台阶、浮空平台和一道沟，都是带 BoxCollider2D 的方块。"))
    S.append(Scene("still", 9, chapter=ch, frames=[str(us / "3_player.png")], crop_from=UFULL,
                   crop_to=aspect((1000, 90, 930, 890)), zoom_start=0.8, zoom_at=1.8,
                   highlights=[(1336, 300, 590, 360, 2.0)],
                   caption="新建 Player：加 BoxCollider2D 和 FittedPlatformerController 组件\n"
                           "（会自动带上 Rigidbody2D，运行时切成 Kinematic）。Inspector 里就是拟合出的参数，可以随时调。"))
    S.append(Scene("still", 7, chapter=ch, frames=[str(us / "4_json.png")],
                   crop_from=aspect((1000, 90, 930, 890)), highlights=[(1336, 664, 590, 36, 0.4)],
                   caption="可选：把 fitted_controller.json 拖到 Fitted Params Json，\n游戏启动时用它覆盖参数，以后重新拟合只需替换这个文件。"))
    S.append(Scene("still", 4, chapter=ch, frames=[str(us / "90_playing.png")], crop_from=UFULL,
                   highlights=[(935, 52, 110, 34, 0.3), (440, 112, 890, 660, 1.0)],
                   caption="点击 Play。"))
    uf = seq(work / "unity_frames")
    S.append(Scene("frames", chapter=ch, frames=uf, keys=keys_from_csv(work / "unity_frames" / "frames.csv", len(uf)),
                   keys_note="按键由演示脚本按时间表输入",
                   caption="运行：←/→（或 A/D）移动，空格跳跃。轻点空格跳上第一级台阶，\n长按跳上更高的台阶和浮空平台，再越过沟，最后原地矮跳和高跳。"))
    # ------------------------------------------------------------------ ④ Godot
    ch = "④ Godot 实操"
    gs = work / "godot_shots"
    S.append(Scene("card", 3.5, chapter=ch, title="同一个控制器放进 Godot 4",
                   lines=["编辑器操作同样由插件脚本自动完成"]))
    S.append(Scene("still", 7, chapter=ch, frames=[str(gs / "0_script.png")], crop_from=GFULL,
                   highlights=[(4, 760, 290, 30, 0.5), (500, 400, 1130, 480, 1.2)],
                   caption="把导出的 fitted_platformer_controller.gd 放进项目。\n同样，拟合出的参数就在脚本开头，都是可以在 Inspector 里改的 @export 变量。"))
    S.append(Scene("still", 6, chapter=ch, frames=[str(gs / "1_scene.png")], crop_from=GFULL,
                   highlights=[(4, 150, 290, 400, 0.5)],
                   caption="场景：地面、台阶和平台用 StaticBody2D + CollisionShape2D。"))
    S.append(Scene("still", 8, chapter=ch, frames=[str(gs / "2_player.png")], crop_from=GFULL,
                   crop_to=aspect((900, 90, 1030, 800)), zoom_start=0.8, zoom_at=1.8,
                   highlights=[(1650, 252, 272, 476, 2.0)],
                   caption="玩家是 CharacterBody2D + CollisionShape2D，挂上这个脚本即可。\n"
                           "Pixels per Unit 把“角色身高”单位换算成 Godot 的像素。"))
    gf = seq(work / "godot_frames", "f0*.png")
    S.append(Scene("frames", chapter=ch, frames=gf, keys=keys_from_csv(work / "godot_frames" / "frames.csv", len(gf)),
                   keys_note="按键由演示脚本按时间表输入",
                   caption="运行（F5）：方向键移动，空格跳跃。和 Unity 是同一串按键输入。"))
    n = min(len(uf), len(gf))
    S.append(Scene("split", frames=uf[90:n - 30], frames2=gf[90:n - 30], labels=("Unity 6", "Godot 4"),
                   keys=keys_from_csv(work / "unity_frames" / "frames.csv", n)[90:],
                   caption="同一个控制器、同一串输入，在 Unity 和 Godot 里的运动几乎一模一样：\n"
                           "两边移植的是工具里的同一个精确积分器，并且都在引擎里实测验证过。"))
    # ------------------------------------------------------------------ ⑤ summary
    S.append(Scene("card", 11, chapter="⑤ 小结", title="结果",
                   lines=["合成测试（已知真实参数）：自动恢复的 8 个参数误差都在 5% 以内",
                          "真实录像 SuperTux / Secret Maryo Chronicles：每段轨迹误差 1.4% – 3.3%",
                          "导出的 Unity / Godot 脚本在引擎中实测，运动与工具里的模型一致",
                          "目前范围：2D 横版的跑、停、转身、各种跳跃、下落与空中控制"]))
    S.append(Scene("card", 8, title="Reference Controller Fitter",
                   lines=["参考录像：SuperTux 关卡录像（Dabmasterars、Dexxor 录制）",
                          "SuperTux © SuperTux Development Team，GPL · 来自 Wikimedia Commons",
                          "Unity、Godot 为其各自所有者的商标"]))
    return S


def main():
    work = Path(sys.argv[1]); out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    preview = "--preview" in sys.argv
    app = QApplication([])
    scenes = build(work)
    if preview:
        from compose import W, H
        from PySide6.QtGui import QImage
        pv = out / "preview"; pv.mkdir(exist_ok=True)
        for f in pv.glob("*.png"):
            f.unlink()

        class Grab(Composer):
            def __init__(self):
                self.n = 0; self.srt = []; self.cache = {}; self.frames = []

            def emit(self, im):
                self.frames.append(im.copy()); self.n += 1
        total = 0.0
        for i, sc in enumerate(scenes):
            g = Grab()
            full_dur = sc.dur
            g.render(sc)
            pick = g.frames[int(len(g.frames) * 0.8)]
            pick.save(str(pv / f"{i:02d}.png"))
            total += len(g.frames) / FPS
        print(f"{len(scenes)} scenes, {total:.1f} s")
        return
    comp = Composer(out / "reference_controller_fitter_demo.mp4")
    for i, sc in enumerate(scenes):
        comp.render(sc)
        print(f"scene {i:02d} done, t={comp.n / FPS:6.1f}s", flush=True)
    comp.close()
    srt = []
    for k, (a, b, text) in enumerate(comp.srt, 1):
        srt.append(f"{k}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n")
    (out / "reference_controller_fitter_demo.zh.srt").write_text("\n".join(srt), encoding="utf-8")
    print("total", comp.n / FPS, "s")


if __name__ == "__main__":
    main()
