"""Sidebar navigation + page shell used by every page."""

import reflex as rx

from ui.state import AuthState

NAV: list[tuple[str, str]] = [
    ("Setup", "/"),
    ("Agents", "/agents"),
    ("Testsets", "/testsets"),
    ("Settings", "/settings"),
    ("Run", "/run"),
    ("Results", "/results"),
]


def _nav_link(label: str, href: str, active: str) -> rx.Component:
    is_active = label == active
    return rx.link(
        label,
        href=href,
        width="100%",
        padding="8px 12px",
        border_radius="8px",
        background="var(--accent-3)" if is_active else "transparent",
        color="var(--accent-11)" if is_active else "var(--gray-12)",
        font_weight="600" if is_active else "400",
        text_decoration="none",
        _hover={"background": "var(--gray-3)"},
    )


def sidebar(active: str) -> rx.Component:
    return rx.vstack(
        rx.heading("Teams Parity", size="5", padding="4px 12px 12px"),
        *[_nav_link(label, href, active) for label, href in NAV],
        rx.spacer(),
        rx.badge(AuthState.status_label, color_scheme="gray", margin="12px"),
        width="220px",
        min_width="220px",
        height="100vh",
        padding="16px 8px",
        spacing="1",
        border_right="1px solid var(--gray-6)",
        background="var(--gray-2)",
        align="start",
        position="sticky",
        top="0",
    )


def page_shell(title: str, *children: rx.Component, active: str = "") -> rx.Component:
    return rx.hstack(
        sidebar(active),
        rx.box(
            rx.heading(title, size="6", margin_bottom="16px"),
            *children,
            padding="24px 32px",
            width="100%",
            max_width="1200px",
        ),
        spacing="0",
        width="100%",
        align="start",
    )
