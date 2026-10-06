"""Continuous detection timing shared by script modules and global guards."""


def sustained_detection(state: dict, detected: bool | None, condition: str,
                        duration_ms: int, now: float) -> bool:
    config = (condition, duration_ms)
    if state.get("config") != config:
        state.clear()
        state["config"] = config
    if detected is None or detected != (condition == "present"):
        state.pop("since", None)
        state.pop("fired", None)
        return False
    since = state.setdefault("since", now)
    if not state.get("fired") and (now - since) * 1000 >= duration_ms:
        state["fired"] = True
        return True
    return False
