"""Minimal HWPX (한글 OWPML) writer for plain transcript documents."""
import io
import re
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape

NS = (
    'xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
    'xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph" '
    'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
    'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
    'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
    'xmlns:hhs="http://www.hancom.co.kr/hwpml/2011/history" '
    'xmlns:hm="http://www.hancom.co.kr/hwpml/2011/master-page" '
    'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:opf="http://www.idpf.org/2007/opf/" '
    'xmlns:ooxmlchart="http://www.hancom.co.kr/hwpml/2016/ooxmlchart" '
    'xmlns:hwpunitchar="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar" '
    'xmlns:epub="http://www.idpf.org/2007/ops" '
    'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0"'
)
XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
FONT = "맑은 고딕"
LANGS = ["HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER"]

# charPr ids
CHAR_BODY, CHAR_TITLE, CHAR_TIME, CHAR_META = 0, 1, 2, 3
# paraPr ids
PARA_BODY, PARA_TITLE = 0, 1

_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _t(text: str) -> str:
    return escape(_INVALID_XML.sub("", text or ""))


def _lang_attrs(value) -> str:
    return " ".join(f'{l.lower()}="{value}"' for l in LANGS)


def _char_pr(cid, height, color="#000000", bold=False) -> str:
    return (
        f'<hh:charPr id="{cid}" height="{height}" textColor="{color}" shadeColor="none" '
        f'useFontSpace="0" useKerning="0" symMark="NONE" borderFillIDRef="2">'
        f'<hh:fontRef {_lang_attrs(0)}/><hh:ratio {_lang_attrs(100)}/><hh:spacing {_lang_attrs(0)}/>'
        f'<hh:relSz {_lang_attrs(100)}/><hh:offset {_lang_attrs(0)}/>'
        + ("<hh:bold/>" if bold else "")
        + '<hh:underline type="NONE" shape="SOLID" color="#000000"/><hh:strikeout shape="NONE" color="#000000"/>'
        '<hh:outline type="NONE"/><hh:shadow type="NONE" color="#B2B2B2" offsetX="10" offsetY="10"/></hh:charPr>'
    )


def _para_pr(pid, align, line_spacing, next_space) -> str:
    margin = (
        '<hh:margin><hc:intent value="0" unit="HWPUNIT"/><hc:left value="0" unit="HWPUNIT"/>'
        '<hc:right value="0" unit="HWPUNIT"/><hc:prev value="0" unit="HWPUNIT"/>'
        f'<hc:next value="{next_space}" unit="HWPUNIT"/></hh:margin>'
        f'<hh:lineSpacing type="PERCENT" value="{line_spacing}" unit="HWPUNIT"/>'
    )
    return (
        f'<hh:paraPr id="{pid}" tabPrIDRef="0" condense="0" fontLineHeight="0" snapToGrid="1" '
        'suppressLineNumbers="0" checked="0">'
        f'<hh:align horizontal="{align}" vertical="BASELINE"/><hh:heading type="NONE" idRef="0" level="0"/>'
        '<hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="KEEP_WORD" widowOrphan="0" '
        'keepWithNext="0" keepLines="0" pageBreakBefore="0" lineWrap="BREAK"/>'
        '<hh:autoSpacing eAsianEng="0" eAsianNum="0"/>'
        '<hp:switch><hp:case hp:required-namespace="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar">'
        f'{margin}</hp:case><hp:default>{margin}</hp:default></hp:switch>'
        '<hh:border borderFillIDRef="1" offsetLeft="0" offsetRight="0" offsetTop="0" offsetBottom="0" '
        'connect="0" ignoreMargin="0"/></hh:paraPr>'
    )


def _border_fill(bid, brush=False) -> str:
    sides = "".join(
        f'<hh:{s}Border type="NONE" width="0.1 mm" color="#000000"/>' for s in ("left", "right", "top", "bottom")
    )
    return (
        f'<hh:borderFill id="{bid}" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
        '<hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/>'
        f'{sides}<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/>'
        + ('<hc:fillBrush><hc:winBrush faceColor="none" hatchColor="#999999" alpha="0"/></hc:fillBrush>' if brush else "")
        + "</hh:borderFill>"
    )


def _header_xml() -> str:
    fontfaces = "".join(
        f'<hh:fontface lang="{l}" fontCnt="1"><hh:font id="0" face="{FONT}" type="TTF" isEmbedded="0">'
        '<hh:typeInfo familyType="FCAT_GOTHIC" weight="6" proportion="4" contrast="0" strokeVariation="1" '
        'armStyle="1" letterform="1" midline="1" xHeight="1"/></hh:font></hh:fontface>'
        for l in LANGS
    )
    chars = [
        _char_pr(CHAR_BODY, 1100),
        _char_pr(CHAR_TITLE, 1600, bold=True),
        _char_pr(CHAR_TIME, 1100, color="#1F4E79", bold=True),
        _char_pr(CHAR_META, 950, color="#555555"),
    ]
    paras = [
        _para_pr(PARA_BODY, "JUSTIFY", 160, 400),
        _para_pr(PARA_TITLE, "CENTER", 160, 1200),
    ]
    return (
        f'{XML_DECL}<hh:head {NS} version="1.4" secCnt="1">'
        '<hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" equation="1"/>'
        f'<hh:refList><hh:fontfaces itemCnt="{len(LANGS)}">{fontfaces}</hh:fontfaces>'
        f'<hh:borderFills itemCnt="2">{_border_fill(1)}{_border_fill(2, brush=True)}</hh:borderFills>'
        f'<hh:charProperties itemCnt="{len(chars)}">{"".join(chars)}</hh:charProperties>'
        '<hh:tabProperties itemCnt="1"><hh:tabPr id="0" autoTabLeft="0" autoTabRight="0"/></hh:tabProperties>'
        f'<hh:paraProperties itemCnt="{len(paras)}">{"".join(paras)}</hh:paraProperties>'
        '<hh:styles itemCnt="1"><hh:style id="0" type="PARA" name="바탕글" engName="Normal" '
        'paraPrIDRef="0" charPrIDRef="0" nextStyleIDRef="0" langID="1042" lockForm="0"/></hh:styles>'
        '</hh:refList><hh:compatibleDocument targetProgram="HWP201X"><hh:layoutCompatibility/></hh:compatibleDocument>'
        '<hh:docOption><hh:linkinfo path="" pageInherit="0" footnoteInherit="0"/></hh:docOption>'
        '<hh:trackchageConfig flags="56"/></hh:head>'
    )


SEC_PR = (
    '<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" tabStop="8000" tabStopVal="4000" '
    'tabStopUnit="HWPUNIT" outlineShapeIDRef="0" memoShapeIDRef="0" textVerticalWidthHead="0" masterPageCnt="0">'
    '<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>'
    '<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
    '<hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0" border="SHOW_ALL" '
    'fill="SHOW_ALL" hideFirstPageNum="0" hideFirstEmptyLine="0" showLineNumber="0"/>'
    '<hp:lineNumberShape restartType="0" countBy="0" distance="0" startNumber="0"/>'
    '<hp:pagePr landscape="WIDELY" width="59528" height="84188" gutterType="LEFT_ONLY">'
    '<hp:margin header="4252" footer="4252" gutter="0" left="8504" right="8504" top="5668" bottom="4252"/></hp:pagePr>'
    '<hp:footNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
    '<hp:noteLine length="-1" type="SOLID" width="0.12 mm" color="#000000"/>'
    '<hp:noteSpacing betweenNotes="283" belowLine="567" aboveLine="850"/>'
    '<hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="EACH_COLUMN" beneathText="0"/></hp:footNotePr>'
    '<hp:endNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
    '<hp:noteLine length="14692344" type="SOLID" width="0.12 mm" color="#000000"/>'
    '<hp:noteSpacing betweenNotes="0" belowLine="567" aboveLine="850"/>'
    '<hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="END_OF_DOCUMENT" beneathText="0"/></hp:endNotePr>'
    + "".join(
        f'<hp:pageBorderFill type="{t}" borderFillIDRef="1" textBorder="PAPER" headerInside="0" footerInside="0" '
        'fillArea="PAPER"><hp:offset left="1417" right="1417" top="1417" bottom="1417"/></hp:pageBorderFill>'
        for t in ("BOTH", "EVEN", "ODD")
    )
    + "</hp:secPr>"
    '<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" sameSz="1" sameGap="0"/></hp:ctrl>'
)


def _para(runs, para_id=PARA_BODY, first=False) -> str:
    """runs: list of (charPrID, text)."""
    body = ""
    if first:
        body += f'<hp:run charPrIDRef="{runs[0][0] if runs else 0}">{SEC_PR}</hp:run>'
    for cid, text in runs:
        body += f'<hp:run charPrIDRef="{cid}"><hp:t>{_t(text)}</hp:t></hp:run>'
    if not runs:
        body += '<hp:run charPrIDRef="0"/>'
    return f'<hp:p id="0" paraPrIDRef="{para_id}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">{body}</hp:p>'


def build_hwpx(title: str, meta_lines, items) -> bytes:
    """items: list of dicts with 'time' and 'text'. Returns the .hwpx file bytes."""
    paras = [_para([(CHAR_TITLE, title)], PARA_TITLE, first=True)]
    for line in meta_lines:
        paras.append(_para([(CHAR_META, line)]))
    paras.append(_para([]))
    for it in items:
        spk = f"[{it['speaker']}] " if it.get("speaker") else ""
        paras.append(_para([(CHAR_TIME, f"[{it.get('time', '')}] {spk}"), (CHAR_BODY, it.get("text", ""))]))
    section = f'{XML_DECL}<hs:sec {NS}>{"".join(paras)}</hs:sec>'

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    content_hpf = (
        f'{XML_DECL}<opf:package {NS} version="" unique-identifier="" id="">'
        f'<opf:metadata><opf:title>{_t(title)}</opf:title><opf:language>ko</opf:language>'
        '<opf:meta name="creator" content="text">YouTube Live Transcriber</opf:meta>'
        f'<opf:meta name="CreatedDate" content="text">{now}</opf:meta>'
        f'<opf:meta name="ModifiedDate" content="text">{now}</opf:meta></opf:metadata>'
        '<opf:manifest><opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>'
        '<opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>'
        '<opf:item id="settings" href="settings.xml" media-type="application/xml"/></opf:manifest>'
        '<opf:spine><opf:itemref idref="header" linear="yes"/><opf:itemref idref="section0" linear="yes"/></opf:spine>'
        '</opf:package>'
    )
    files = {
        "version.xml": (
            f'{XML_DECL}<hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" '
            'tagetApplication="WORDPROCESSOR" major="5" minor="1" micro="0" buildNumber="1" os="1" '
            'xmlVersion="1.4" application="Hancom Office Hangul" appVersion="10, 0, 0, 0 WIN32LEWindows_10"/>'
        ),
        "META-INF/container.xml": (
            f'{XML_DECL}<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container" '
            'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf"><ocf:rootfiles>'
            '<ocf:rootfile full-path="Contents/content.hpf" media-type="application/hwpml-package+xml"/>'
            '<ocf:rootfile full-path="Preview/PrvText.txt" media-type="text/plain"/>'
            '</ocf:rootfiles></ocf:container>'
        ),
        "META-INF/manifest.xml": (
            f'{XML_DECL}<odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>'
        ),
        "Contents/content.hpf": content_hpf,
        "Contents/header.xml": _header_xml(),
        "Contents/section0.xml": section,
        "settings.xml": (
            f'{XML_DECL}<ha:HWPApplicationSetting xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
            'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0">'
            '<ha:CaretPosition listIDRef="0" paraIDRef="0" pos="0"/></ha:HWPApplicationSetting>'
        ),
        "Preview/PrvText.txt": "\r\n".join(
            [title] + list(meta_lines) + [
                f"[{i.get('time', '')}] " + (f"[{i['speaker']}] " if i.get('speaker') else "") + f"{i.get('text', '')}"
                for i in items[:50]
            ]
        )[:1024],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        # mimetype must be the first entry and stored uncompressed
        z.writestr(zipfile.ZipInfo("mimetype"), "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in files.items():
            z.writestr(name, data.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()
