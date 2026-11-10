"""화면 공통 모양(피그마 'BridgeDocs 메인페이지' 디자인 기준 색·글꼴·카드).

위젯에 objectName이나 property를 붙여 모양을 고른다:
  objectName  card(흰 카드) · pageTitle · pageSub · sectionTitle · muted · eyebrow · nav(위쪽 메뉴 버튼)
  property    variant = "primary"(파란 버튼) | "ghost"(테두리 버튼) | "green" ; badge = "ok" | "warn" | "fail" | "info"
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

BLUE, BLUE_DARK, BLUE_SOFT = "#356DE8", "#245ED8", "#EAF1FF"
GREEN, GREEN_SOFT = "#2F8F5F", "#E5F5ED"
INK, TEXT, MUTED = "#121C31", "#172033", "#6B7488"
BG, CARD, LINE = "#F7F9FC", "#FFFFFF", "#E7EBF1"
WARN, WARN_SOFT, FAIL, FAIL_SOFT = "#B7791F", "#FFF6E5", "#D64545", "#FDECEC"

QSS = f"""
QWidget {{ color: {TEXT}; font-size: 13px; }}
QMainWindow, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}
#header {{ background: {CARD}; border-bottom: 1px solid {LINE}; }}
#footer {{ background: {CARD}; border-top: 1px solid {LINE}; }}
#logo {{ font-size: 20px; font-weight: 800; color: {INK}; }}
#logoMark {{ background: {BLUE}; color: white; border-radius: 9px; font-size: 16px; font-weight: 800; }}
#online {{ color: {MUTED}; font-size: 12px; }}
QPushButton#nav {{ background: transparent; border: none; border-bottom: 3px solid transparent;
                   padding: 18px 12px 15px; font-size: 14px; font-weight: 600; color: #4A5468; border-radius: 0; }}
QPushButton#nav:hover {{ color: {BLUE}; }}
QPushButton#nav:checked {{ color: {BLUE_DARK}; background: {BLUE_SOFT}; border-bottom-color: {BLUE}; }}

#card {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 14px; }}
#taskCard {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 14px; }}
#taskCard:hover {{ background: {BLUE_SOFT}; border-color: {BLUE}; }}
#drop:hover {{ background: {BLUE_SOFT}; border-color: {BLUE}; }}
#banner {{ background: #EEF3FD; border: 1px solid #DCE6FA; border-radius: 14px; }}
#notice {{ background: {FAIL_SOFT}; border: 1px solid #F5C2C2; border-radius: 10px; color: {FAIL}; }}
#noticeInfo {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; color: {MUTED}; }}
#darkBar {{ background: {INK}; border-radius: 10px; color: white; }}
#drop {{ background: {CARD}; border: 1.5px dashed #B9C6E4; border-radius: 14px; }}
#drop[hover="true"] {{ background: {BLUE_SOFT}; border-color: {BLUE}; }}
#guide {{ background: #F1F5FD; border-radius: 10px; }}
#hint {{ background: #FFF8E8; border: 1px solid #F6E3B4; border-radius: 10px; color: #7A5A16; }}

#pageTitle {{ font-size: 26px; font-weight: 700; color: {INK}; }}
#pageSub, #muted {{ color: {MUTED}; }}
#crumb {{ color: {MUTED}; font-size: 12px; }}
#heroTitle {{ font-size: 44px; font-weight: 800; color: {INK}; }}
#heroBlue {{ font-size: 44px; font-weight: 800; color: {BLUE}; }}
#sectionTitle {{ font-size: 15px; font-weight: 700; color: {INK}; }}
#cardTitle {{ font-size: 22px; font-weight: 800; color: {INK}; }}
#eyebrow {{ font-size: 11px; font-weight: 800; color: {BLUE}; letter-spacing: 1px; }}
#eyebrowGreen {{ font-size: 11px; font-weight: 800; color: {GREEN}; letter-spacing: 1px; }}
#pill {{ background: {BLUE_SOFT}; color: {BLUE_DARK}; border-radius: 13px; padding: 5px 14px; font-weight: 600; }}
#stepNum {{ background: {BLUE}; color: white; border-radius: 15px; font-weight: 700; }}
#stepNumDone {{ background: {GREEN}; color: white; border-radius: 15px; font-weight: 700; }}
#stepNo {{ background: {BLUE_SOFT}; color: {BLUE}; border-radius: 15px; font-weight: 700; }}
#iconBox {{ background: {BLUE_SOFT}; color: {BLUE}; border-radius: 12px; font-size: 20px; font-weight: 700; }}
#iconBoxGreen {{ background: {GREEN_SOFT}; color: {GREEN}; border-radius: 12px; font-size: 20px; font-weight: 700; }}
#chip {{ background: #F1F4F8; color: #5A6478; border-radius: 6px; padding: 3px 8px; font-size: 11px; font-weight: 700; }}

QPushButton {{ background: {CARD}; border: 1px solid #D5DCE7; border-radius: 9px; padding: 8px 16px; font-weight: 600; }}
QPushButton:hover {{ border-color: {BLUE}; color: {BLUE_DARK}; }}
QPushButton:disabled {{ color: #A9B1C0; border-color: {LINE}; }}
QPushButton[variant="primary"] {{ background: {BLUE}; color: white; border: none; padding: 10px 20px; }}
QPushButton[variant="primary"]:hover {{ background: {BLUE_DARK}; color: white; }}
QPushButton[variant="danger"] {{ background: {FAIL}; color: white; border: none; padding: 10px 20px; }}
QPushButton[variant="danger"]:hover {{ background: #B93838; color: white; }}
QPushButton[variant="link"]{{ background: transparent; border: none; color: {BLUE_DARK}; padding: 4px 0; font-weight: 700; }}
QPushButton[variant="linkGreen"] {{ background: transparent; border: none; color: {GREEN}; padding: 4px 0; font-weight: 700; }}
QPushButton[seg="true"] {{ background: transparent; border: none; border-radius: 7px; padding: 6px 12px; color: {MUTED}; font-weight: 700; }}
QPushButton[seg="true"]:checked {{ background: {CARD}; color: {BLUE_DARK}; }}
#segBox {{ background: #EEF2F8; border-radius: 9px; }}

QLabel[badge="ok"] {{ background: {GREEN_SOFT}; color: {GREEN}; border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 700; }}
QLabel[badge="warn"] {{ background: {WARN_SOFT}; color: {WARN}; border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 700; }}
QLabel[badge="fail"] {{ background: {FAIL_SOFT}; color: {FAIL}; border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 700; }}
QLabel[badge="info"] {{ background: #F1F4F8; color: {MUTED}; border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 700; }}

QListWidget, QTableWidget, QTreeWidget, QPlainTextEdit {{ background: {CARD}; border: none; border-radius: 10px; }}
QListWidget::item {{ border: 1px solid {LINE}; border-radius: 10px; padding: 10px; margin: 4px 2px; }}
QListWidget::item:selected {{ background: #EEF3FD; border-color: #A9C1F5; color: {TEXT}; }}
QHeaderView::section {{ background: #F5F7FB; color: {MUTED}; border: none; border-bottom: 1px solid {LINE};
                        padding: 8px; font-size: 12px; font-weight: 700; }}
QTableWidget, QTreeWidget {{ gridline-color: {LINE}; selection-background-color: #E4ECFD; selection-color: {TEXT}; }}
QTableWidget::item, QTreeWidget::item {{ padding: 6px; border-bottom: 1px solid #EFF2F7; }}
QComboBox, QLineEdit {{ background: {CARD}; border: 1px solid #D5DCE7; border-radius: 9px; padding: 8px 10px; }}
QComboBox:focus, QLineEdit:focus {{ border-color: {BLUE}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {LINE}; selection-background-color: {BLUE_SOFT};
                               selection-color: {TEXT}; }}
QCheckBox {{ spacing: 6px; }}
QSplitter::handle {{ background: transparent; }}
QScrollArea {{ border: none; }}
"""


def apply(app: QApplication) -> None:
    font = QFont("Noto Sans KR")
    font.setFamilies(["Noto Sans KR", "Malgun Gothic", "맑은 고딕"])     # Noto Sans KR이 없는 PC는 맑은 고딕
    font.setPointSize(10)
    app.setFont(font)
    app.setStyleSheet(QSS)


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def badge(text: str, kind: str) -> QLabel:
    w = QLabel(text)
    w.setProperty("badge", kind)
    return w


def button(text: str, variant: str = "") -> QPushButton:
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    return b


def card(name: str = "card") -> tuple[QFrame, QVBoxLayout]:
    """둥근 흰 카드와 그 안의 세로 배치."""
    f = QFrame()
    f.setObjectName(name)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(20, 18, 20, 18)
    lay.setSpacing(10)
    return f, lay


def page_head(title: str, sub: str, *right: QWidget):
    """페이지 위쪽 한 줄: 큰 제목 — 설명, 오른쪽 버튼들."""
    from PySide6.QtWidgets import QHBoxLayout
    row = QHBoxLayout()
    row.addWidget(label(title, "pageTitle"))
    row.addWidget(label(f"—  {sub}", "pageSub"), 0, Qt.AlignBottom)
    row.addStretch(1)
    for w in right:
        row.addWidget(w)
    return row


def repolish(w: QWidget) -> None:
    """property를 바꾼 뒤 모양을 다시 적용."""
    w.style().unpolish(w)
    w.style().polish(w)
