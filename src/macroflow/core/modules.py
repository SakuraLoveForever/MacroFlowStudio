"""Pure module references shared by editing and execution."""
from macroflow.core.models import END_CURRENT_SCRIPT_LABEL
from macroflow.core.storage import registered_module_object

def module_reference_binding(key: str, obj: dict | None = None) -> dict:
    """Return the stable image/module binding carried by inserted actions."""
    obj = obj if obj is not None else (registered_module_object(key) or {})
    raw_region = obj.get("region", [])
    region = []
    if isinstance(raw_region, (list, tuple)) and len(raw_region) == 4:
        try:
            parts = [int(part) for part in raw_region]
        except (TypeError, ValueError):
            parts = []
        if len(parts) == 4 and parts[2] > 0 and parts[3] > 0:
            region = parts
    return {
        "template": str(obj.get("template") or key),
        "module_key": key,
        "module_ref": True,
        "module_category": str(obj.get("category") or "switch"),
        "region_mode": "template",
        "region": region,
    }


def action_with_live_module_binding(action: dict | None) -> dict:
    """Refresh editable action fields from its current module object."""
    updated = dict(action or {})
    if not updated.get("module_ref"):
        return updated
    key = str(updated.get("module_key", "")).strip()
    obj = registered_module_object(key) if key else None
    if obj is None:
        return updated
    updated.update(module_reference_binding(key, obj))
    return updated


def module_action_for_key(key: str, category: str, obj: dict | None = None) -> dict:
    """Build the live-reference action stored when a module is inserted."""
    if category == "special":
        return {
            "type": "end_current_script"
            if key == END_CURRENT_SCRIPT_LABEL
            else "restart_workflow"
        }
    obj = obj if obj is not None else (registered_module_object(key) or {})
    binding = module_reference_binding(key, obj)
    if category in ("workflow_global", "script_global", "global"):
        return {
            "type": "global_detect", **binding,
            "module_category": (
                "workflow_global" if category == "global" else category
            ), "delay_ms": 0,
        }
    action = {
        "type": "image_match", **binding,
        "module_category": "switch", "delay_ms": 0,
        "on_found": "continue", "on_timeout": "continue",
    }
    if obj.get("recognize") == "number":
        # 数字模块插入脚本时由行编辑框补比较值；相等默认跳转，失败默认继续下一行。
        action["on_found"] = "jump"
    return action
