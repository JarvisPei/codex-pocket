"""Explicit host capabilities; never infer execution ownership from OS labels."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BridgeRuntime:
    platform: str = "macos"
    execution_mode: str = "desktop"
    desktop_control: bool = True
    attachments: bool = True
    native_text_send: bool = False
    native_new_tasks: bool = False
    attachment_paths: bool = False

    def __post_init__(self) -> None:
        if self.native_new_tasks and not self.native_text_send:
            raise ValueError('Native task creation requires native text delivery')
        if self.execution_mode not in {"desktop", "background"}:
            raise ValueError("Unknown bridge execution mode")
        if not self.desktop_control and self.execution_mode == "desktop":
            raise ValueError("Desktop execution needs a Desktop controller")
        if self.attachment_paths and (not self.native_text_send or not self.attachments):
            raise ValueError('File handoff requires native text delivery and uploads')
        if self.execution_mode == "background" and self.attachments and not self.attachment_paths:
            raise ValueError("Background preview supports text only")

    def capabilities(self) -> dict:
        result = {
            "platform": self.platform,
            "executionMode": self.execution_mode,
            "desktopControl": self.desktop_control,
            "attachments": self.attachments,
        }
        if self.native_text_send:
            result.update(nativeTextSend=True, newTasks=self.native_new_tasks, nativeReceiptPolling=True)
        if self.attachment_paths:
            result['attachmentMode'] = 'localPaths'
        return result


WINDOWS_PREVIEW = BridgeRuntime(
    platform="windows", execution_mode="background",
    desktop_control=False, attachments=False,
)

# Existing-thread text is intercepted by WindowsNativeHandler. No full Desktop
# control capability is claimed, and no fallback starts an app-server turn.
WINDOWS_NATIVE_TEXT = BridgeRuntime(
    platform="windows", execution_mode="background", desktop_control=False,
    attachments=False, native_text_send=True,
)
