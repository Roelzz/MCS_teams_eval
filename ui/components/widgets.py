"""Small shared UI building blocks."""

from __future__ import annotations

import reflex as rx


def card(*children: rx.Component, **props) -> rx.Component:
    return rx.box(
        *children,
        padding="20px",
        border="1px solid var(--gray-6)",
        border_radius="12px",
        background="var(--color-panel-solid)",
        width="100%",
        **props,
    )


def field(label: str, control: rx.Component, hint: str = "") -> rx.Component:
    return rx.vstack(
        rx.text(label, font_weight="600", font_size="0.85em"),
        control,
        rx.cond(hint != "", rx.text(hint, color="var(--gray-11)", font_size="0.75em")),
        spacing="1",
        align="start",
        width="100%",
    )


def log_console(lines: rx.Var, height: str = "260px") -> rx.Component:
    return rx.box(
        rx.foreach(
            lines,
            lambda ln: rx.text(ln, font_family="monospace", font_size="0.78em", white_space="pre"),
        ),
        height=height,
        overflow_y="auto",
        background="#0f172a",
        color="#e2e8f0",
        padding="12px",
        border_radius="8px",
        width="100%",
    )
