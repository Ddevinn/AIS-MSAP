from __future__ import annotations

import hashlib
import os
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image as PILImage, ImageEnhance, ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "artifacts" / "user-guide-20261015"
SCREEN = WORK / "screenshots"
OUT_DIR = ROOT / "output" / "pdf"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT = OUT_DIR / "AIS数据分析与态势评估平台_用户使用指南_V0.1.0-alpha.1.pdf"
SOURCE = Path(r"C:\Users\Devin\Desktop\AIS数据分析与态势评估平台_用户使用指南_V0.1.0-alpha.1.docx")

PAGE_W, PAGE_H = A4
NAVY = colors.HexColor("#102A43")
BLUE = colors.HexColor("#2E68E8")
CYAN = colors.HexColor("#13A8C7")
INK = colors.HexColor("#172B4D")
MUTED = colors.HexColor("#5F7187")
LINE = colors.HexColor("#D8E2EC")
PANEL = colors.HexColor("#F3F7FB")
SOFT_BLUE = colors.HexColor("#EAF2FF")
GREEN = colors.HexColor("#198754")
ORANGE = colors.HexColor("#E56A00")


def register_fonts() -> None:
    normal = Path(r"C:\Windows\Fonts\msyh.ttc")
    bold = Path(r"C:\Windows\Fonts\msyhbd.ttc")
    if not normal.exists():
        normal = Path(r"C:\Windows\Fonts\simhei.ttf")
    if not bold.exists():
        bold = normal
    pdfmetrics.registerFont(TTFont("GuideCN", str(normal), subfontIndex=0))
    pdfmetrics.registerFont(TTFont("GuideCN-Bold", str(bold), subfontIndex=0))
    pdfmetrics.registerFontFamily("GuideCN", normal="GuideCN", bold="GuideCN-Bold")


register_fonts()


styles = getSampleStyleSheet()
styles.add(ParagraphStyle(
    name="GuideTitle", fontName="GuideCN-Bold", fontSize=25, leading=34,
    textColor=colors.white, spaceAfter=8,
))
styles.add(ParagraphStyle(
    name="GuideSubtitle", fontName="GuideCN", fontSize=13, leading=20,
    textColor=colors.HexColor("#D7E7FF"),
))
styles.add(ParagraphStyle(
    name="Heading1CN", parent=styles["Heading1"], fontName="GuideCN-Bold",
    fontSize=19, leading=25, textColor=NAVY, spaceBefore=3, spaceAfter=10,
    keepWithNext=True,
))
styles.add(ParagraphStyle(
    name="Heading2CN", parent=styles["Heading2"], fontName="GuideCN-Bold",
    fontSize=13.5, leading=19, textColor=BLUE, spaceBefore=10, spaceAfter=6,
    keepWithNext=True,
))
styles.add(ParagraphStyle(
    name="Heading3CN", parent=styles["Heading3"], fontName="GuideCN-Bold",
    fontSize=10.5, leading=15, textColor=INK, spaceBefore=7, spaceAfter=4,
    keepWithNext=True,
))
styles.add(ParagraphStyle(
    name="BodyCN", parent=styles["BodyText"], fontName="GuideCN",
    fontSize=9.2, leading=15.2, textColor=INK, spaceAfter=5,
))
styles.add(ParagraphStyle(
    name="BodySmall", parent=styles["BodyText"], fontName="GuideCN",
    fontSize=8.2, leading=13, textColor=MUTED, spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="BulletCN", parent=styles["BodyText"], fontName="GuideCN",
    fontSize=9.1, leading=14.5, textColor=INK, leftIndent=14, firstLineIndent=-10,
    bulletIndent=2, spaceAfter=3,
))
styles.add(ParagraphStyle(
    name="StepCN", parent=styles["BodyText"], fontName="GuideCN",
    fontSize=9.1, leading=14.5, textColor=INK, leftIndent=18, firstLineIndent=-14,
    spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="CaptionCN", parent=styles["BodyText"], fontName="GuideCN",
    fontSize=8.2, leading=12.5, alignment=TA_CENTER, textColor=MUTED,
    spaceBefore=4, spaceAfter=8,
))
styles.add(ParagraphStyle(
    name="Callout", parent=styles["BodyText"], fontName="GuideCN",
    fontSize=9, leading=14.5, textColor=NAVY, borderColor=colors.HexColor("#B8D0F8"),
    borderWidth=0.7, borderPadding=8, backColor=SOFT_BLUE, spaceBefore=5, spaceAfter=8,
))
styles.add(ParagraphStyle(
    name="TableHead", fontName="GuideCN-Bold", fontSize=8.2, leading=11,
    textColor=colors.white, alignment=TA_CENTER,
))
styles.add(ParagraphStyle(
    name="TableCell", fontName="GuideCN", fontSize=7.8, leading=11.2,
    textColor=INK,
))
styles.add(ParagraphStyle(
    name="TOCHeading", fontName="GuideCN-Bold", fontSize=21, leading=28,
    textColor=NAVY, spaceAfter=14,
))
styles.add(ParagraphStyle(
    name="TOC1", fontName="GuideCN-Bold", fontSize=10, leading=16,
    textColor=INK, leftIndent=0, firstLineIndent=0, spaceBefore=3,
))
styles.add(ParagraphStyle(
    name="TOC2", fontName="GuideCN", fontSize=8.8, leading=14,
    textColor=MUTED, leftIndent=16, firstLineIndent=0,
))


def p(text: str, style: str = "BodyCN") -> Paragraph:
    return Paragraph(text, styles[style])


def bullet(text: str) -> Paragraph:
    return Paragraph(f"• {text}", styles["BulletCN"])


def step(number: int, text: str) -> Paragraph:
    return Paragraph(f"<b>{number}.</b> {text}", styles["StepCN"])


def h1(text: str) -> Paragraph:
    return Paragraph(text, styles["Heading1CN"])


def h2(text: str) -> Paragraph:
    return Paragraph(text, styles["Heading2CN"])


def h3(text: str) -> Paragraph:
    return Paragraph(text, styles["Heading3CN"])


def make_table(headers, rows, widths, font_size=7.8):
    head = [Paragraph(escape(str(x)), styles["TableHead"]) for x in headers]
    body = [[Paragraph(escape(str(x)), ParagraphStyle(
        name=f"Cell{font_size}", parent=styles["TableCell"], fontSize=font_size,
        leading=font_size + 3.2,
    )) for x in row] for row in rows]
    table = Table([head] + body, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.45, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PANEL]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def image_flow(path: Path, max_width=495, max_height=350) -> Image:
    with PILImage.open(path) as im:
        width, height = im.size
    ratio = min(max_width / width, max_height / height)
    return Image(str(path), width=width * ratio, height=height * ratio)


def figure(path: Path, caption: str, max_width=495, max_height=350):
    return KeepTogether([
        image_flow(path, max_width=max_width, max_height=max_height),
        p(caption, "CaptionCN"),
    ])


def crop_marker_panels() -> tuple[Path, Path]:
    outputs = []
    for src_name, dst_name in [
        ("05-marker-visible.png", "05-marker-visible-panel.png"),
        ("06-marker-hidden.png", "06-marker-hidden-panel.png"),
    ]:
        src = SCREEN / src_name
        dst = SCREEN / dst_name
        with PILImage.open(src) as im:
            crop = im.crop((1160, 95, 1535, 650))
            crop = ImageOps.expand(crop, border=2, fill="#D8E2EC")
            crop.save(dst)
        outputs.append(dst)
    return outputs[0], outputs[1]


VISIBLE_PANEL, HIDDEN_PANEL = crop_marker_panels()


class GuideDocTemplate(BaseDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            style = flowable.style.name
            if style in {"Heading1CN", "Heading2CN"}:
                level = 0 if style == "Heading1CN" else 1
                text = flowable.getPlainText()
                key = f"bookmark-{self.seq.nextf('heading')}"
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, level=level, closed=False)
                self.notify("TOCEntry", (level, text, self.page, key))


def draw_cover(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    ship_source = ROOT / "frontend" / "assets" / "login-container-ship.webp"
    cover_ship = WORK / "cover-ship.jpg"
    if not cover_ship.exists():
        with PILImage.open(ship_source).convert("RGB") as im:
            im = ImageOps.fit(im, (1600, 920), method=PILImage.Resampling.LANCZOS, centering=(0.5, 0.55))
            im = ImageEnhance.Brightness(im).enhance(0.65)
            im.save(cover_ship, quality=92)
    canvas.drawImage(str(cover_ship), 0, 0, width=PAGE_W, height=440, preserveAspectRatio=False, mask="auto")
    canvas.setFillColor(colors.Color(8/255, 33/255, 59/255, alpha=0.34))
    canvas.rect(0, 0, PAGE_W, 440, fill=1, stroke=0)

    logo = ROOT / "frontend" / "assets" / "ais-logo.png"
    canvas.drawImage(str(logo), 48, PAGE_H - 105, width=46, height=46, mask="auto")
    canvas.setFillColor(colors.white)
    canvas.setFont("GuideCN-Bold", 12)
    canvas.drawString(108, PAGE_H - 78, "AIS数据分析与态势评估平台")
    canvas.setFillColor(colors.HexColor("#72D7F4"))
    canvas.setFont("GuideCN", 7.5)
    canvas.drawString(108, PAGE_H - 93, "MARITIME ANALYSIS WORKSPACE")

    canvas.setFont("GuideCN-Bold", 26)
    canvas.setFillColor(colors.white)
    canvas.drawString(48, PAGE_H - 185, "用户使用指南")
    canvas.setFont("GuideCN", 13)
    canvas.setFillColor(colors.HexColor("#CFE4FF"))
    canvas.drawString(48, PAGE_H - 215, "数据加载 · 清洗 · 地图分析 · 交通流统计 · 结果导出")

    canvas.setFillColor(colors.HexColor("#2E68E8"))
    canvas.roundRect(48, PAGE_H - 285, 190, 34, 8, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("GuideCN-Bold", 10)
    canvas.drawString(63, PAGE_H - 273, "V0.1.0-alpha.1 · 原型验证版")

    canvas.setFillColor(colors.white)
    canvas.setFont("GuideCN-Bold", 20)
    canvas.drawString(48, 350, "从每一个位置，看见航行的全貌。")
    canvas.setFont("GuideCN", 9.5)
    canvas.setFillColor(colors.HexColor("#D9E8F6"))
    canvas.drawString(48, 320, "本指南使用“样例数据.xlsx”演示最新平台操作流程。")

    canvas.setFillColor(colors.white)
    canvas.setFont("GuideCN", 9)
    canvas.drawRightString(PAGE_W - 48, 42, "编制日期：2026年10月")
    canvas.restoreState()


def draw_body(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(44, PAGE_H - 43, PAGE_W - 44, PAGE_H - 43)
    canvas.setFont("GuideCN", 7.6)
    canvas.setFillColor(MUTED)
    canvas.drawString(44, PAGE_H - 32, "AIS数据分析与态势评估平台 · 用户使用指南")
    canvas.drawRightString(PAGE_W - 44, PAGE_H - 32, "V0.1.0-alpha.1")
    canvas.line(44, 38, PAGE_W - 44, 38)
    canvas.drawString(44, 25, "原型验证版 · 以最新平台界面为准")
    canvas.drawRightString(PAGE_W - 44, 25, f"第 {doc.page} 页")
    canvas.restoreState()


def build_story():
    story = [Spacer(1, 1), PageBreak()]

    story += [
        h1("用户使用指南说明"),
        h2("适用系统"),
        p("本指南适用于 AIS 数据分析与态势评估平台 Windows 10/11 x64 本机交付版。平台通过“启动平台.bat”启动，推荐使用最新版 Microsoft Edge 或 Google Chrome 访问本机地址 http://127.0.0.1:8000。"),
        h2("适用使用人群"),
        p("本指南适用于需要使用 AIS 历史数据开展教学、科研、业务分析和系统维护的相关人员，主要包括："),
        bullet("高校与科研机构人员：交通运输、航海技术、港航工程、地理信息等相关专业的教师、科研人员和学生。"),
        bullet("AIS 与交通数据分析人员：从事数据清洗、轨迹分析、交通流统计、空间分析和可视化工作的人员。"),
        bullet("港航与海事业务人员：需要了解航道、港区和重点水域船舶活动特征及运行态势的业务人员。"),
        bullet("系统建设与维护人员：负责平台部署、运行维护、账号管理、数据备份和版本更新的人员。"),
        h2("当前版本介绍"),
        p("当前版本为 <b>V0.1.0-alpha.1（原型验证版）</b>，用于功能演示、需求确认和试用反馈。平台已经覆盖 AIS 数据接入与清洗、轨迹与活动密度展示、区域与截面交通流统计、行为识别、统计图表、用户管理和结果导出等主要流程。"),
        p("本指南使用“样例数据.xlsx”演示操作。样例源文件包含 6,116 条记录；清洗后包含 6,042 个 AIS 点、50 艘船和 88 条轨迹片段。截图中的数字会随筛选范围变化，仅作为操作示例。", "Callout"),
        h2("后续更新与迭代"),
        p("平台后续将根据实际使用反馈和业务需求持续更新迭代，可能调整界面布局、字段规则、分析方法、统计口径和操作步骤。使用新版本时，请同时查阅对应版本的用户指南与更新说明。"),
        PageBreak(),
    ]

    toc = TableOfContents()
    toc.levelStyles = [styles["TOC1"], styles["TOC2"]]
    story += [p("目录", "TOCHeading"), toc, PageBreak()]

    story += [
        h1("1 平台介绍"),
        h2("1.1 平台简介"),
        p("AIS 数据分析与态势评估平台用于读取和整理 AIS 历史数据，并在同一工作台中完成数据加载、质量清洗、条件筛选、轨迹展示、空间范围分析、活动密度分析、船舶行为识别、统计图表查看和结果导出。"),
        p("典型流程为：启动平台 → 登录 → 新增或选择数据 → 加载数据 → 按需清洗 → 设置筛选条件 → 查看地图与统计 → 导出统计表。"),
        h2("1.2 核心功能"),
        make_table(
            ["功能模块", "主要能力"],
            [
                ["数据接入与预处理", "上传 CSV、Excel、PKL/PICKLE；自动识别字段、时区与编码；生成可查询数据集"],
                ["轨迹分析", "按船舶、时间、速度、船型和水域查看历史轨迹、活动范围与运动信息"],
                ["行为分析", "识别靠泊、锚泊、转向、直航和变速等典型行为"],
                ["交通流统计", "按区域或截面统计船舶、轨迹、AIS 点、穿越次数、方向与平均航速"],
                ["态势展示与导出", "展示密度、航向、船型、航速、时间序列等结果，并导出 Excel 统计表"],
            ],
            [40*mm, 126*mm],
        ),
        h2("1.3 应用场景"),
        bullet("航道与港区态势分析：查看指定时间和空间范围内的船舶活动、航速和船型结构。"),
        bullet("截面交通流分析：绘制航道截面，统计穿越次数、方向及参与船舶。"),
        bullet("重点水域活动分析：用多边形、矩形或圆形分析港池、锚地和作业区。"),
        bullet("单船轨迹复核：按 MMSI 查看轨迹，比较对地航向与船首向的时间变化。"),
        bullet("数据质量检查与成果输出：核对导入、清洗结果并导出当前范围的统计表。"),
        h2("1.4 使用限制"),
        bullet("平台处理历史 AIS 文件，不提供实时接入、轨迹预测、碰撞预警或自动风险评分。"),
        bullet("底图来自 OpenStreetMap，需要网络连接；底图失败不影响本机数据处理。"),
        bullet("行为识别基于阈值规则，应结合轨迹、时间与业务水域复核。"),
        bullet("PKL/PICKLE 可能包含可执行对象，只上传来源可信且用途明确的文件。"),
        PageBreak(),
    ]

    concepts = [
        ["AIS", "船舶自动识别系统；平台处理已采集的历史观测记录"],
        ["MMSI", "海上移动业务识别码，通常为 9 位数字"],
        ["AIS 点", "一条带时间、经纬度和运动信息的观测记录"],
        ["数据集", "一个源文件加载后形成的可查询数据及其登记信息"],
        ["加载", "识别字段、转换时间与数值，并生成查询所需结构"],
        ["清洗", "执行校验、异常剔除、轨迹分段和派生字段计算"],
        ["轨迹片段", "同一船舶在连续时段内的一组有序 AIS 点"],
        ["COG / course", "对地航向，描述船舶实际移动方向，单位为度"],
        ["heading", "船首向，描述船头指向，单位为度，可缺失"],
        ["截面", "由两个端点构成的线段，用于统计真实穿越"],
        ["区域", "多边形、矩形或圆形范围，用于筛选范围内 AIS 点"],
        ["空间密度", "当前条件下 AIS 观测点的空间聚集程度"],
    ]
    fields = [
        ["mmsi", "是", "船舶标识", "MMSI、vessel_mmsi、船舶编号"],
        ["timestamp", "是", "观测时间", "updatetime、datetime、更新时间"],
        ["lon / lat", "是", "经纬度，十进制度", "lon、lng、longitude；lat、latitude"],
        ["speed", "是", "对地航速，kn", "speed、SOG、航速"],
        ["course", "是", "对地航向，度", "course、COG、对地航向"],
        ["ship_type", "否", "船型代码", "type、shiptype、船型"],
        ["length / width", "否", "船长 / 船宽，m", "length、width、beam、船长、船宽"],
        ["draught", "否", "吃水，m", "draught、draft、吃水"],
        ["heading", "否", "船首向，度", "heading、true_heading、船首向"],
    ]
    story += [
        h1("1.5 基本概念与数据要求"),
        make_table(["概念", "说明"], concepts, [36*mm, 130*mm]),
        Spacer(1, 8),
        h2("标准字段"),
        p("表头匹配忽略大小写、空格、下划线和标点，列顺序不影响识别。缺少必需字段或字段存在歧义时，平台终止加载并提示修改源文件表头。"),
        make_table(["标准字段", "必需", "含义", "常用表头示例"], fields, [27*mm, 15*mm, 43*mm, 81*mm], font_size=7.3),
        PageBreak(),
    ]

    story += [
        h1("2 平台使用"),
        h2("2.1 用户登录"),
        p("操作前提：交付 ZIP 已完整解压到可写目录，平台服务已启动，并且已有可用账号。全新安装首次运行时，由负责人在本机初始化首个超级管理员。"),
        step(1, "双击“启动平台.bat”，等待浏览器自动打开；也可在 Edge 或 Chrome 访问 http://127.0.0.1:8000。"),
        step(2, "在登录页输入管理员分配的账号和密码，点击“登录平台”。"),
        step(3, "首次使用临时密码时，按提示设置不少于 10 个字符的新密码。"),
        step(4, "登录成功后，从“历史数据”选择记录，或点击“新增文件”。"),
        figure(SCREEN / "01-login.png", "图 1　平台登录入口。登录标题下方的附加说明已移除。", max_height=292),
        PageBreak(),
    ]

    story += [
        h2("2.2 数据读取与加载"),
        p("平台网页上传统一支持 <b>.csv、.xlsx、.xls、.pkl 和 .pickle</b>。所有已登录用户使用同一个文件选择入口，不再区分管理员上传权限，也不需要手动输入服务端本机路径。"),
        step(1, "在左侧“加载数据”中点击“新增文件”。"),
        step(2, "在“数据名称”填写“样例数据”；无时区日期保持“北京时间”，CSV 编码保持“自动识别”。"),
        step(3, "点击“选择 Excel / CSV / PKL 文件”，在系统文件窗口选择“样例数据.xlsx”。"),
        step(4, "等待上传与字段识别完成，核对时间、经纬度、航速、course 和 heading。"),
        step(5, "点击“加载数据”，等待状态变为“数据加载完成”。"),
        p("CSV 第一行应为字段名；Excel 表头前不要放说明文字、合并标题或空白行。PKL/PICKLE 仅用于兼容既有轨迹对象，必须来自可信来源。", "Callout"),
        Table([
            [image_flow(SCREEN / "02-add-dataset.png", max_width=205, max_height=394),
             p("<b>本图检查要点</b><br/><br/>• 数据名称为“样例数据”<br/>• 上传按钮包含 PKL<br/>• 页面没有本机路径输入框<br/>• 上传成功后仍需点击“加载数据”<br/><br/><b>上传与加载是两个独立步骤。</b>", "BodyCN")],
        ], colWidths=[72*mm, 94*mm], style=[
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ("BACKGROUND", (1, 0), (1, 0), PANEL),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]),
        p("图 2　新增文件面板与统一文件选择入口。", "CaptionCN"),
        PageBreak(),
    ]

    clean_rows = [
        ["最大航速", "50 kn", "剔除上报航速超过阈值的记录"],
        ["异常跳点速度", "50 kn", "按相邻坐标和时间识别跳点或漂移"],
        ["最大加速度", "8 kn/min", "剔除航速变化率过大的点"],
        ["长时间中断", "60 min", "间隔过大时划分新的轨迹片段"],
        ["长距离中断", "3 海里", "距离过大时划分新的轨迹片段"],
        ["停留 / 低速", "0.5 / 3 kn", "生成航行状态与行为辅助字段"],
    ]
    story += [
        h2("2.3 数据清洗"),
        p("清洗针对整个当前数据集，不受页面筛选条件限制。清洗结果单独保存，原始文件与加载结果仍保留。"),
        step(1, "确认当前数据为“样例数据”，并且已加载。"),
        step(2, "先使用默认参数；确有业务依据时再展开“高级清洗设置”。"),
        step(3, "点击“开始清洗”，等待进度达到 100%；处理中不要切换数据或关闭平台。"),
        step(4, "核对输入、输出、剔除点数、轨迹片段数和异常分类。"),
        step(5, "需要对比时点击“恢复清洗前数据”。"),
        make_table(["参数", "默认值", "作用"], clean_rows, [40*mm, 28*mm, 98*mm]),
        Spacer(1, 8),
        Table([[image_flow(SCREEN / "03-cleaning.png", max_width=190, max_height=400),
                p("<b>样例结果</b><br/><br/>源文件：6,116 条<br/>清洗输出：6,042 点<br/>剔除：74 点<br/>船舶：50 艘<br/>轨迹片段：88 条<br/><br/>清洗不会补齐缺失观测，也不会进行轨迹插值。", "BodyCN")]],
              colWidths=[68*mm, 98*mm], style=[
                  ("VALIGN", (0, 0), (-1, -1), "TOP"),
                  ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                  ("BACKGROUND", (1, 0), (1, 0), PANEL),
                  ("PADDING", (0, 0), (-1, -1), 8),
              ]),
        p("图 3　清洗状态、质量摘要与清洗结果。", "CaptionCN"),
        PageBreak(),
    ]

    filter_rows = [
        ["开始 / 结束时间", "日期时间", "限定观测时间范围"],
        ["最低 / 最高航速", "数值，kn", "限定上报航速"],
        ["船舶类型", "下拉选择", "按 AIS 船型代码区间筛选"],
        ["船舶编号", "MMSI", "查看指定船舶"],
        ["经纬度范围", "最小值和最大值", "使用矩形包围范围筛选"],
        ["空间标记", "截面或区域", "通过地图工具应用"],
        ["船舶行为", "行为下拉选择", "控制行为轨迹显示"],
    ]
    story += [
        h2("2.4 条件筛选与联动"),
        p("筛选条件会联动总览指标、轨迹、空间密度、统计图表与导出结果。修改输入框后必须点击“应用筛选”，仅修改数值不会立即生效。"),
        make_table(["条件", "输入方式", "说明"], filter_rows, [47*mm, 42*mm, 77*mm]),
        h2("2.5 轨迹展示与地图控制"),
        step(1, "使用地图左上角控件缩放和平移；点击“回到数据范围”恢复当前数据边界。"),
        step(2, "用“船舶轨迹”和“空间密度”按钮独立显示或隐藏图层。"),
        step(3, "点击“刷新图层”重新请求轨迹与密度；底图失败时点击“重新加载底图”。"),
        step(4, "点击下方坐标切换十进制度与度分秒；需要时点击“全屏查看”。"),
        figure(SCREEN / "04-workbench-map.png", "图 4　样例数据的轨迹与活动密度。底图、轨迹与密度为独立图层。", max_height=315),
        PageBreak(),
    ]

    marker_rows = [
        ["点", "1 个坐标", "记录位置，不作为统计范围"],
        ["截面", "2 个不同坐标", "统计真实相交、穿越次数和正反向"],
        ["路线", "至少 2 个坐标", "记录参考路线，不作为统计范围"],
        ["区域", "多边形至少 3 点；矩形或圆形按界面提示", "按点是否位于区域内统计"],
    ]
    eye_table = Table([
        [image_flow(VISIBLE_PANEL, max_width=225, max_height=280), image_flow(HIDDEN_PANEL, max_width=225, max_height=280)],
        [p("显示：眼睛图标", "CaptionCN"), p("隐藏：斜线眼睛图标", "CaptionCN")],
    ], colWidths=[83*mm, 83*mm], style=[
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
        ("BACKGROUND", (0, 1), (-1, 1), PANEL),
        ("PADDING", (0, 0), (-1, -1), 5),
    ])
    story += [
        h2("2.6 标记与空间分析"),
        p("地图支持点、截面、路线和区域标记；区域可使用任意多边形、矩形或圆形。截面与区域可作为统计条件。"),
        make_table(["标记类型", "绘制要求", "主要用途"], marker_rows, [28*mm, 62*mm, 76*mm]),
        h3("创建标记"),
        step(1, "在地图右侧选择标记工具，再选择点、截面、路线或区域。"),
        step(2, "输入名称、分类和颜色；点击“开始绘制”后在地图加入节点，或批量输入经纬度。"),
        step(3, "点击“完成”保存；标记保存在当前浏览器中。"),
        h3("显示与隐藏区域 / 截面"),
        p("区域和截面标记默认显示。点击标记名称右侧、编辑按钮左侧的眼睛图标即可隐藏；图标会切换为带斜线的眼睛。再次点击恢复显示。隐藏只影响地图图形，不会删除标记，仍可在标记库中编辑、删除或用于空间统计。"),
        eye_table,
        p("图 5　区域标记的显示与隐藏状态；截面标记使用相同图标。", "CaptionCN"),
        PageBreak(),
    ]

    story += [
        h2("2.6.1 区域交通流统计"),
        step(1, "先创建区域标记。本例使用“海口湾重点水域”。"),
        step(2, "点击筛选工具，在“筛选方式”中选择“按区域范围”。"),
        step(3, "选择区域标记并点击“查看该标记”。"),
        step(4, "核对船舶数、轨迹数、区域内 AIS 点和平均航速；完成后点击“查看全部”。"),
        figure(SCREEN / "07-region-traffic.png", "图 6　区域交通流示例：44 艘船、67 条轨迹、1,962 个 AIS 点，平均航速 8.84 kn。", max_height=310),
        p("区域模式按 AIS 点是否落入边界内筛选。矩形和圆形在保存时转换为闭合边界，统计口径与多边形一致。", "Callout"),
        PageBreak(),
    ]

    story += [
        h2("2.6.2 截面交通流统计"),
        step(1, "创建由两个端点组成的截面。本例使用“海口湾航道截面”。"),
        step(2, "点击筛选工具，在“筛选方式”中选择“按截面穿越”。"),
        step(3, "选择截面并点击“查看该标记”。"),
        step(4, "核对船舶数、轨迹数、穿越次数、平均航速以及正向 / 反向次数。"),
        figure(SCREEN / "08-section-traffic.png", "图 7　截面交通流示例：19 艘船、22 条轨迹、23 次穿越，其中正向 11 次、反向 12 次。", max_height=310),
        p("穿越次数是轨迹与截面线段的真实相交次数，不等于独立船舶数；同一艘船可能多次穿越。正反向由轨迹移动方向与截面端点顺序共同确定。", "Callout"),
        PageBreak(),
    ]

    behavior_rows = [
        ["靠泊", "上报航速 ≤ 0.5 kn，推算移动速度 ≤ 0.2 kn"],
        ["锚泊", "上报航速 ≤ 0.5 kn，推算移动速度 > 0.2 kn"],
        ["转向", "相邻有效点的 COG 变化 ≥ 15°"],
        ["直航", "COG 变化 ≤ 5°，且加速度 < 0.5 kn/min"],
        ["变速", "加速度绝对值 ≥ 0.5 kn/min"],
    ]
    story += [
        h2("2.7 空间密度分析"),
        p("空间密度显示 AIS 观测点的聚集位置，可在“平滑热力”和“计数网格”之间切换，并设置约 100 m 至 5 km 的网格精度和不透明度。"),
        step(1, "确认地图中的“空间密度”图层已开启。"),
        step(2, "用“平滑热力”观察连续趋势，或用“计数网格”查看各网格的相对数量。"),
        step(3, "局部分析选择较细网格，大范围分析选择较粗网格；点击“重新计算密度”。"),
        figure(SCREEN / "09-density-grid.png", "图 8　计数网格示例。颜色表示 AIS 点相对数量，不直接等于船舶数量。", max_height=305),
        h2("2.8 船舶行为识别"),
        p("在地图行为工具中选择行为并点击“显示相应轨迹”。结果属于规则筛查，应结合时间、MMSI、区域和原始轨迹复核。"),
        make_table(["行为", "默认识别依据"], behavior_rows, [40*mm, 126*mm]),
        PageBreak(),
    ]

    story += [
        h2("2.9 统计图表与航向分析"),
        p("统计区展示当前条件下的总览指标、航速分布、船型结构、交通流时间序列、船长分布、航行状态、对地航向 COG、船首向 heading 及单船时间曲线。"),
        step(1, "应用筛选后点击顶部“刷新统计”或底部“重新计算当前统计”。"),
        step(2, "在航向分析中选择 15°、30°、45°或 90°分箱，比较有效点、缺失点、主要方向区间和圆周平均方向。"),
        step(3, "查看单船时间曲线时输入 9 位 MMSI。本例使用 353195000。"),
        figure(SCREEN / "10a-direction-distributions.png", "图 9　样例船舶的 COG 与 heading 分布；百分比以各自有效点为分母。", max_height=205),
        figure(SCREEN / "10b-direction-time.png", "图 10　MMSI 353195000 的 COG 与 heading 时间曲线。", max_height=175),
        p("COG 表示相对地面的实际移动方向，heading 表示船首指向，两者不一定相同。COG 的 360°和 heading 的 511 作为不可用值；跨越 0°/360°、轨迹断点或长间隔时曲线断开。", "Callout"),
        PageBreak(),
    ]

    story += [
        h2("2.10 统计结果导出"),
        step(1, "选择当前数据并完成必要清洗。"),
        step(2, "应用时间、航速、船型、MMSI、经纬度或空间标记条件。"),
        step(3, "核对页面总览与统计图表，选择所需航向分箱。"),
        step(4, "点击顶部“导出统计表”，保存 Excel 文件。"),
        p("导出文件包含综合统计、航速分布、船型分布、船长分布、交通流、航行状态、航向统计、COG 分布和 heading 分布等工作表。导出使用全部符合条件的数据。"),
        h2("2.11 历史数据与个人账号"),
        bullet("“历史数据”只显示本人记录，可按数据名称或文件名搜索。"),
        bullet("“使用此数据”选择已加载数据；如有清洗结果，自动使用清洗结果。"),
        bullet("“加载此数据”选择待加载记录并启动转换。"),
        bullet("“清空当前选择”只取消当前会话选择，不删除记录和文件。"),
        bullet("“移除记录”删除本人历史登记；磁盘文件保留，但界面没有撤销入口。"),
        bullet("修改密码成功后当前会话结束，需要重新登录；关闭浏览器不一定立即使会话失效。"),
        p("归档时建议同时记录数据名称、清洗参数、筛选条件、空间标记和导出日期。", "Callout"),
        PageBreak(),
    ]

    admin_rows = [
        ["用户账号", "创建账号、修改显示名称、角色和启用状态"],
        ["登录记录", "账号、登录时间、成功或失败、访问 IP、浏览器"],
        ["访问记录", "账号、访问时间、页面和访问 IP"],
        ["有效会话", "登录时间、最近活动、IP、浏览器，可强制结束"],
        ["管理操作记录", "操作人、时间、类型、目标用户、IP 和详情"],
    ]
    story += [
        h1("3 用户管理"),
        p("用户管理只对超级管理员开放。普通用户登录分析平台后不会看到管理入口。"),
        h2("3.1 登录与页面结构"),
        step(1, "使用超级管理员账号登录分析工作台，在账号菜单点击“用户管理”；也可访问 /admin/login。"),
        step(2, "输入超级管理员账号和密码，进入“AIS 用户管理平台”。"),
        figure(SCREEN / "11-admin.png", "图 11　用户账号列表与管理入口；后台时间按北京时间显示。", max_height=285),
        make_table(["页签", "主要内容"], admin_rows, [44*mm, 122*mm]),
        PageBreak(),
    ]

    story += [
        h2("3.2 创建与维护用户"),
        h3("创建用户"),
        step(1, "点击“创建用户”，输入账号、显示名称和临时密码。"),
        step(2, "选择“普通用户”或“超级管理员”，点击“保存”。"),
        p("账号使用 3 至 40 位字母、数字、下划线、点或短横线；临时密码至少 10 个字符。新账号默认要求首次登录修改临时密码。"),
        h3("编辑角色与账号状态"),
        step(1, "在用户列表找到目标用户，点击“编辑”。"),
        step(2, "修改显示名称、角色或“启用此账号”，然后保存。"),
        p("禁用账号或调整角色会结束该账号已有会话。当前管理员不能禁用或降级自己；平台至少保留一个启用的超级管理员。"),
        h3("重置密码"),
        step(1, "点击目标用户的“重置密码”，输入不少于 10 个字符的新密码。"),
        step(2, "通常保留“要求用户下次登录修改密码”，点击“重置并结束已有会话”。"),
        h3("记录与会话"),
        p("使用用户筛选查看指定账号的记录。需要强制退出账号时，在“有效会话”页点击“结束会话”。会话默认有效期为 12 小时，最近活动时间最多每 30 秒更新一次。"),
        p("账号、会话、历史登记和审计记录保存在交付目录的本地数据库中。备份或迁移前应等待任务完成、停止平台，再备份整个交付目录。", "Callout"),
        PageBreak(),
    ]

    faq = [
        ("4.1 加载的数据文件格式有要求吗", "所有已登录用户都可在网页选择 CSV、XLSX、XLS、PKL 或 PICKLE。CSV 建议使用 UTF-8 或 GB18030/GBK。Excel 第一行应为字段名。PKL/PICKLE 只使用可信来源文件。"),
        ("4.2 哪些字段是必需的", "必需字段为 MMSI、时间、经度、纬度、航速和对地航向。船型、船长、船宽、吃水和船首向为可选字段。"),
        ("4.3 字段名称不完全一致怎么办", "平台按常见中英文别名自动匹配，并忽略大小写、空格、下划线和标点。存在缺失或歧义时应修改源文件表头后重新上传。"),
        ("4.4 未识别字段会怎样", "未映射字段会在预览中列出，但不会进入标准分析字段；源文件不会被修改。"),
        ("4.5 可选字段缺失会影响加载吗", "通常不会。缺少 heading 仍可分析 COG；缺失船型或尺寸会影响相应分布与统计。"),
        ("4.6 CSV 编码错误怎么办", "将编码从“自动识别”改为“UTF-8”或“GB18030 / GBK”后重试，或用表格软件另存为 UTF-8 CSV。"),
        ("4.7 时间为何相差若干小时", "无时区日期按导入时选择的时区解释，默认 Asia/Shanghai；带 UTC 偏移的日期保留真实时刻。"),
        ("4.8 上传后为什么还不能查看", "上传只登记文件，还需点击“加载数据”完成字段转换和数据集构建。"),
        ("4.9 为什么仍需要清洗", "加载负责字段识别和类型转换；清洗负责异常剔除、顺序检查与轨迹分段，两者目的不同。"),
        ("4.10 清洗后点数明显减少怎么办", "核对异常类别和清洗参数，重点检查最大航速、跳点速度、加速度和时间解释；必要时恢复清洗前数据对比。"),
    ]
    story += [h1("4 常见问题")]
    for title, answer in faq:
        story += [h2(title), p(answer)]
    story += [PageBreak()]

    faq2 = [
        ("4.11 筛选修改后图表没有变化", "修改输入框后需点击“应用筛选”；空间标记需点击“查看该标记”，行为条件需点击“显示相应轨迹”。"),
        ("4.12 底图失败是否表示数据丢失", "不是。底图需要网络；轨迹、密度和统计由本机服务处理。检查网络后点击“重新加载底图”。"),
        ("4.13 密度为何不等于船舶数量", "颜色主要反映 AIS 点数量。上报频率不同会造成差异，应结合独立船舶数与总览指标。"),
        ("4.14 COG 与 heading 为什么不同", "COG 是实际移动方向，heading 是船首指向。横流、侧滑、低速漂移或传感器缺失都可能造成差异。"),
        ("4.15 行为识别与人工判断不同", "当前使用固定阈值，不使用泊位、锚地、天气或人工标注；请结合轨迹和业务水域复核。"),
        ("4.16 导出与页面曲线点数不同", "导出使用全部符合条件的数据；单船时间曲线可能为显示性能限制点数。"),
        ("4.17 历史数据提示找不到文件", "移动、重命名交付目录或手工删除文件会使路径失效。恢复原路径或重新上传加载。"),
        ("4.18 忘记密码或账号被禁用", "联系超级管理员在后台重置密码或启用账号。平台不显示原密码，也不提供邮箱或短信找回。"),
        ("4.19 关闭浏览器后平台是否停止", "没有。平台运行在“启动平台.bat”窗口中；完成任务后在该窗口按 Ctrl+C 停止服务。"),
        ("4.20 隐藏标记后为什么仍能统计", "显隐只控制地图图形。区域或截面仍保留在标记库中，也可继续作为空间统计条件；需要删除时使用垃圾桶按钮。"),
    ]
    for title, answer in faq2:
        story += [h2(title), p(answer)]

    story += [
        Spacer(1, 12),
        p("<b>完成使用前检查</b>", "Callout"),
        bullet("确认数据名称、清洗状态、筛选条件和空间标记与本次任务一致。"),
        bullet("导出统计表并记录导出日期。"),
        bullet("退出账号；任务完成后在启动窗口按 Ctrl+C 停止平台。"),
    ]
    return story


def build_pdf():
    cover_frame = Frame(0, 0, PAGE_W, PAGE_H, leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0, id="cover")
    body_frame = Frame(44, 48, PAGE_W - 88, PAGE_H - 101, leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0, id="body")
    doc = GuideDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=44,
        rightMargin=44,
        topMargin=53,
        bottomMargin=48,
        title="AIS数据分析与态势评估平台 用户使用指南",
        author="AIS数据分析与态势评估平台项目组",
        subject="V0.1.0-alpha.1 原型验证版",
    )
    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=[cover_frame], onPage=draw_cover, autoNextPageTemplate="Body"),
        PageTemplate(id="Body", frames=[body_frame], onPage=draw_body),
    ])
    doc.multiBuild(build_story())


if __name__ == "__main__":
    if not SOURCE.exists():
        raise SystemExit(f"Reference document not found: {SOURCE}")
    actual_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest().upper()
    expected_hash = "ECBDCF9CB35249B804A7A8DC721ABA76A38EDCE67100FCC609300647B0C4285C"
    if actual_hash != expected_hash:
        raise SystemExit("Reference document changed; regenerate the template contract before authoring.")
    required = [
        "01-login.png", "02-add-dataset.png", "03-cleaning.png", "04-workbench-map.png",
        "05-marker-visible.png", "06-marker-hidden.png", "07-region-traffic.png",
        "08-section-traffic.png", "09-density-grid.png", "10a-direction-distributions.png",
        "10b-direction-time.png", "11-admin.png",
    ]
    missing = [name for name in required if not (SCREEN / name).exists()]
    if missing:
        raise SystemExit(f"Missing screenshots: {missing}")
    build_pdf()
    print(OUTPUT)
