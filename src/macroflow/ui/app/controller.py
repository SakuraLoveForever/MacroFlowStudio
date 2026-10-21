"""One execution controller, composed independently of Studio authoring."""
from .bundles import BundlesMixin
from .execution import ExecutionMixin
from .global_detect import GlobalDetectMixin
from .guards import GuardsMixin
from .recovery import RecoveryMixin
from .tray import TrayMixin
from .window_binding import WindowBindingMixin
from .runtime.helpers import RuntimeHelpersMixin
from .runtime.hotkeys import RuntimeHotkeysMixin
from .runtime.scripts import RuntimeScriptsMixin
from .runtime.shell import RuntimeShellMixin
from .runtime.workflow import RuntimeWorkflowMixin


class ExecutionController(
    BundlesMixin, RuntimeShellMixin, RuntimeHelpersMixin,
    GlobalDetectMixin, GuardsMixin, TrayMixin, WindowBindingMixin,
    RecoveryMixin, RuntimeScriptsMixin, ExecutionMixin,
    RuntimeWorkflowMixin, RuntimeHotkeysMixin,
):
    """Shared playback, detection, recovery and lifecycle; clients provide views."""

    def _initialize_authoring_state(self):
        self.recorder = None

    def _finish_client_capture(self):
        pass

    def _configure_client_startup(self):
        self.startup_new_script = False
        self.startup_open_script = None
        self.startup_edit_module = None

    def _schedule_client_startup(self, explicit_editor_start):
        pass
