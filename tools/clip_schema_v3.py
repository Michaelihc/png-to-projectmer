#!/usr/bin/env python3
"""Canonical writer / validator / reference evaluator for reinforcements.clip v3.

v3 is THE single runtime animation-clip format: it folds the three legacy
runtime sample formats (reinforcements.emblem-intro-clip v1,
reinforcements.scalar-transform-clip v2, and the untagged world-motion v1)
into one file shape with three parts:

  * ``targets``  — structure only (what is animated and how it binds),
  * ``channels`` — numbers only (fixed-count sample arrays per target+path),
  * ``cues``     — named timestamps with numeric payload only (no verbs).

Interpolation is DECLARED, never inferred: ``time.interpolation`` is the clip
default (``step`` or ``linear``) and a channel may override with ``interp``
(``step`` | ``linear`` | ``slerp``). ``time.startFrame`` generalizes the
crownfall clip's ``startStep`` so the floor-clamp arithmetic
``frame = clamp(floor(t*fps), startFrame, startFrame+n-1)`` is identical.
World-motion clips carry ``time.times`` (explicit knot seconds, one per
sample) so the legacy explicit-time playback is reproduced exactly instead of
being approximated by a uniform grid of the 6-decimal rounded knots.

Every producer imports THIS module to emit (stable key order, ``indent=2``,
trailing newline) so byte-identity generator tests keep working, and every
python consumer (the playback differ, preview tools) imports the evaluator
here so there is exactly one python sampling implementation. The C# runtime
mirror is ``src/Services/Cinematics/ClipSampler.cs``; the two are locked
together by ``tools/diff_clip_playback.py`` + ``tests/CinematicClip.Unit``.

A vendored copy of this file lives in the sibling repo
``../Embel-conversion-fr/tools/clip_schema_v3.py`` (the emblem baker's home);
keep the two byte-identical when editing.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from pathlib import Path
from typing import Any

FORMAT = "reinforcements.clip"
VERSION = 3

INTERPOLATIONS = ("step", "linear", "slerp")
CLIP_DEFAULT_INTERPOLATIONS = ("step", "linear")
TARGET_KINDS = ("group", "primitiveSet", "worldRoot")
PRIMITIVE_SET_OPS = ("rotate_about_pivot", "rotate_local", "translate_local", "screw_local")
PRIMITIVE_SET_AXES = ("right", "up", "forward")
CHANNEL_PATHS_BY_KIND = {
    "group": ("tx", "ty", "rot", "sx", "sy", "opacity"),
    "primitiveSet": ("op.deg", "op.m"),
    "worldRoot": ("pos.x", "pos.y", "pos.z", "rot"),
}

# The floor-clamp nudge shared with the legacy crownfall sampler
# (tools/crownfall_entry_mechanisms_clip.py step_for_time) and the C# mirror.
STEP_EPSILON = 1e-7


# --------------------------------------------------------------------------- checksum

def channels_checksum(clip: dict[str, Any]) -> str:
    """SHA-256 (upper hex) over the canonical channel byte stream."""

    stream = []
    for channel in clip.get("channels", []):
        stream.append([
            channel["target"],
            channel["path"],
            channel.get("interp"),
            int(channel.get("stride", 1)),
            channel["samples"],
        ])
    payload = json.dumps(stream, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()


# --------------------------------------------------------------------------- canonical writer

_GENERATOR_KNOWN = ("tool", "authoredSpec", "authoredSpecSha256", "stamp",
                    "timeBasis", "mode", "staticCameraTime")


def _ordered(source: dict[str, Any], known: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in known:
        if key in source:
            out[key] = source[key]
    for key in sorted(source):
        if key not in out:
            out[key] = source[key]
    return out


def _canonical_target(target: dict[str, Any]) -> dict[str, Any]:
    kind = target["kind"]
    out: dict[str, Any] = {"id": target["id"], "kind": kind}
    if kind == "group":
        out["pivot"] = target["pivot"]
        out["axisDeg"] = target["axisDeg"]
        if target.get("parent") is not None:
            out["parent"] = target["parent"]
        out["bind"] = {"names": list(target["bind"]["names"])}
    elif kind == "primitiveSet":
        out["op"] = target["op"]
        out["axis"] = target["axis"]
        out["pivot"] = target["pivot"]
        if "screwPitchMetersPerDegree" in target:
            out["screwPitchMetersPerDegree"] = target["screwPitchMetersPerDegree"]
        out["bind"] = {"prefixes": [
            {"prefix": item["prefix"], "index": item["index"]}
            for item in target["bind"]["prefixes"]
        ]}
    elif kind == "worldRoot":
        camera = target["staticCamera"]
        out["staticCamera"] = {
            "position": camera["position"],
            "lookAt": camera["lookAt"],
            "up": camera["up"],
            "fov": camera["fov"],
        }
    return out


def canonical_clip(clip: dict[str, Any]) -> dict[str, Any]:
    """Reorder a clip into the canonical serialization order and stamp the checksum."""

    time_block = clip["time"]
    out: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "name": clip["name"],
        "generator": _ordered(dict(clip.get("generator", {})), _GENERATOR_KNOWN),
        "time": _ordered(dict(time_block), ("fps", "startFrame", "sampleCount",
                                            "durationSeconds", "interpolation", "times")),
    }
    if "view" in clip:
        out["view"] = _ordered(dict(clip["view"]), ("heightMeters",))
    out["assets"] = [
        _ordered(dict(asset), ("role", "path", "sha256", "bindingCounts"))
        for asset in clip.get("assets", [])
    ]
    out["targets"] = [_canonical_target(dict(target)) for target in clip.get("targets", [])]
    out["channels"] = [
        _ordered(dict(channel), ("target", "path", "interp", "stride", "samples"))
        for channel in clip.get("channels", [])
    ]
    out["cues"] = [
        _ordered(dict(cue), ("t", "id", "lead", "hold", "until", "value"))
        for cue in clip.get("cues", [])
    ]
    out["checksum"] = {"channels": channels_checksum(out)}
    return out


def dumps_clip(clip: dict[str, Any]) -> str:
    """Canonical text form: validate, then indent=2 + trailing newline."""

    ordered = canonical_clip(clip)
    validate_clip(ordered)
    return json.dumps(ordered, indent=2, allow_nan=False) + "\n"


def write_clip(path: Path, clip: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_clip(clip), encoding="utf-8")


def load_clip(path: Path) -> dict[str, Any]:
    clip = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_clip(clip)
    return clip


# --------------------------------------------------------------------------- validation

def _fail(message: str) -> None:
    raise ValueError(f"reinforcements.clip v3: {message}")


def _require_finite(value: Any, context: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        _fail(f"{context} is not a finite number")
    return float(value)


def _require_vec3(value: Any, context: str) -> None:
    if not isinstance(value, list) or len(value) != 3:
        _fail(f"{context} must be a 3-element array")
    for component in value:
        _require_finite(component, context)


def validate_clip(clip: dict[str, Any]) -> None:
    if clip.get("format") != FORMAT or clip.get("version") != VERSION:
        _fail(f"format/version must be {FORMAT}/{VERSION}, "
              f"found {clip.get('format')!r}/{clip.get('version')!r}")
    for legacy_key in ("groups", "tracks", "samples", "sampleRateHz", "startStep"):
        if legacy_key in clip:
            _fail(f"legacy top-level key '{legacy_key}' must not survive into v3")
    if not isinstance(clip.get("name"), str) or not clip["name"]:
        _fail("name must be a non-empty string")

    time_block = clip.get("time")
    if not isinstance(time_block, dict):
        _fail("time block is missing")
    fps = time_block.get("fps")
    if not isinstance(fps, int) or fps <= 0:
        _fail("time.fps must be a positive integer")
    start_frame = time_block.get("startFrame")
    if not isinstance(start_frame, int) or start_frame < 0:
        _fail("time.startFrame must be a non-negative integer")
    sample_count = time_block.get("sampleCount")
    if not isinstance(sample_count, int) or sample_count < 2:
        _fail("time.sampleCount must be an integer >= 2")
    duration = _require_finite(time_block.get("durationSeconds"), "time.durationSeconds")
    if duration <= 0.0:
        _fail("time.durationSeconds must be positive")
    if time_block.get("interpolation") not in CLIP_DEFAULT_INTERPOLATIONS:
        _fail("time.interpolation must be 'step' or 'linear'")
    times = time_block.get("times")
    if times is not None:
        if not isinstance(times, list) or len(times) != sample_count:
            _fail("time.times must contain exactly time.sampleCount knot seconds")
        previous = None
        for knot in times:
            value = _require_finite(knot, "time.times knot")
            if previous is not None and value < previous:
                _fail("time.times must be non-decreasing")
            previous = value

    if "view" in clip:
        height = _require_finite(clip["view"].get("heightMeters"), "view.heightMeters")
        if height <= 0.0:
            _fail("view.heightMeters must be positive")

    assets = clip.get("assets")
    if not isinstance(assets, list):
        _fail("assets must be a list")
    for asset in assets:
        if not isinstance(asset.get("role"), str) or not asset["role"]:
            _fail("assets[].role must be a non-empty string")
        if not isinstance(asset.get("path"), str) or not asset["path"]:
            _fail("assets[].path must be a non-empty string")
        sha = asset.get("sha256")
        if sha is not None and (not isinstance(sha, str) or len(sha) != 64):
            _fail(f"assets[].sha256 must be 64 hex chars ({asset['path']})")

    targets = clip.get("targets")
    if not isinstance(targets, list) or not targets:
        _fail("targets must be a non-empty list")
    seen_targets: dict[str, str] = {}
    for target in targets:
        target_id = target.get("id")
        if not isinstance(target_id, str) or not target_id or target_id in seen_targets:
            _fail(f"invalid or duplicate target id {target_id!r}")
        kind = target.get("kind")
        if kind not in TARGET_KINDS:
            _fail(f"target '{target_id}' has unsupported kind {kind!r}")
        if kind == "group":
            pivot = target.get("pivot")
            if not isinstance(pivot, list) or len(pivot) != 2:
                _fail(f"group '{target_id}' pivot must be a 2-element array")
            for component in pivot:
                _require_finite(component, f"group '{target_id}' pivot")
            _require_finite(target.get("axisDeg"), f"group '{target_id}' axisDeg")
            parent = target.get("parent")
            if parent is not None and (
                    seen_targets.get(parent) != "group"):
                _fail(f"group '{target_id}' references parent '{parent}' "
                      "before that parent is defined")
            bind = target.get("bind")
            names = bind.get("names") if isinstance(bind, dict) else None
            if not isinstance(names, list) or any(
                    not isinstance(name, str) or not name for name in names):
                _fail(f"group '{target_id}' bind.names must be a list of non-empty strings")
        elif kind == "primitiveSet":
            if target.get("op") not in PRIMITIVE_SET_OPS:
                _fail(f"primitiveSet '{target_id}' has unsupported op {target.get('op')!r}")
            if target.get("axis") not in PRIMITIVE_SET_AXES:
                _fail(f"primitiveSet '{target_id}' has unsupported axis {target.get('axis')!r}")
            _validate_primitive_set_pivot(target_id, target.get("pivot"))
            if target["op"] == "screw_local":
                pitch = target.get("screwPitchMetersPerDegree")
                if (not isinstance(pitch, (int, float)) or isinstance(pitch, bool)
                        or not math.isfinite(pitch) or pitch == 0.0):
                    _fail(f"primitiveSet '{target_id}' screw_local requires a finite "
                          "non-zero screwPitchMetersPerDegree")
            bind = target.get("bind")
            prefixes = bind.get("prefixes") if isinstance(bind, dict) else None
            if not isinstance(prefixes, list) or not prefixes:
                _fail(f"primitiveSet '{target_id}' has no bind.prefixes")
            for item in prefixes:
                prefix = item.get("prefix") if isinstance(item, dict) else None
                index = item.get("index") if isinstance(item, dict) else None
                if (not isinstance(prefix, str) or not prefix
                        or not isinstance(index, int) or index < -1):
                    _fail(f"primitiveSet '{target_id}' has an invalid prefix/index binding")
        elif kind == "worldRoot":
            camera = target.get("staticCamera")
            if not isinstance(camera, dict):
                _fail(f"worldRoot '{target_id}' is missing staticCamera")
            _require_vec3(camera.get("position"), f"worldRoot '{target_id}' position")
            _require_vec3(camera.get("lookAt"), f"worldRoot '{target_id}' lookAt")
            _require_vec3(camera.get("up"), f"worldRoot '{target_id}' up")
            _require_finite(camera.get("fov"), f"worldRoot '{target_id}' fov")
        seen_targets[target_id] = kind

    channels = clip.get("channels")
    if not isinstance(channels, list):
        _fail("channels must be a list")
    seen_channels: set[tuple[str, str]] = set()
    for channel in channels:
        target_id = channel.get("target")
        path = channel.get("path")
        if target_id not in seen_targets:
            _fail(f"channel references unknown target {target_id!r}")
        kind = seen_targets[target_id]
        if path not in CHANNEL_PATHS_BY_KIND[kind]:
            _fail(f"channel '{target_id}.{path}' is not a valid path for kind '{kind}'")
        key = (target_id, path)
        if key in seen_channels:
            _fail(f"duplicate channel '{target_id}.{path}'")
        seen_channels.add(key)
        interp = channel.get("interp")
        if interp is not None and interp not in INTERPOLATIONS:
            _fail(f"channel '{target_id}.{path}' has unsupported interp {interp!r}")
        stride = channel.get("stride", 1)
        if not isinstance(stride, int) or stride < 1:
            _fail(f"channel '{target_id}.{path}' stride must be a positive integer")
        effective = interp or time_block["interpolation"]
        if effective == "slerp" and stride != 4:
            _fail(f"channel '{target_id}.{path}' slerp requires stride 4")
        if effective != "slerp" and stride != 1:
            _fail(f"channel '{target_id}.{path}' stride {stride} requires interp slerp")
        samples = channel.get("samples")
        if not isinstance(samples, list) or len(samples) != sample_count * stride:
            _fail(f"channel '{target_id}.{path}' must contain exactly "
                  f"sampleCount*stride = {sample_count * stride} samples")
        for value in samples:
            _require_finite(value, f"channel '{target_id}.{path}' sample")

    cues = clip.get("cues")
    if not isinstance(cues, list):
        _fail("cues must be a list")
    for cue in cues:
        _require_finite(cue.get("t"), "cue t")
        if not isinstance(cue.get("id"), str) or not cue["id"]:
            _fail("cue id must be a non-empty string")
        for optional in ("lead", "hold", "until", "value"):
            if optional in cue:
                _require_finite(cue[optional], f"cue '{cue['id']}' {optional}")

    checksum = clip.get("checksum")
    if not isinstance(checksum, dict) or not isinstance(checksum.get("channels"), str):
        _fail("checksum.channels is missing")
    actual = channels_checksum(clip)
    if checksum["channels"] != actual:
        _fail(f"checksum.channels mismatch (recorded {checksum['channels'][:8]}.., "
              f"actual {actual[:8]}..)")


def _validate_primitive_set_pivot(target_id: str, pivot: Any) -> None:
    if pivot in ("binding:0", "meanBindings"):
        return
    if not isinstance(pivot, str) or not pivot.startswith("local:"):
        _fail(f"primitiveSet '{target_id}' has invalid pivot {pivot!r}")
    parts = pivot[len("local:"):].split(",")
    if len(parts) != 3:
        _fail(f"primitiveSet '{target_id}' local pivot must contain three coordinates")
    try:
        coordinates = [float(part) for part in parts]
    except ValueError:
        _fail(f"primitiveSet '{target_id}' has a non-numeric local pivot")
    if not all(math.isfinite(value) for value in coordinates):
        _fail(f"primitiveSet '{target_id}' has a non-finite local pivot")


# --------------------------------------------------------------------------- reference evaluator

def channel_interp(clip: dict[str, Any], channel: dict[str, Any]) -> str:
    return channel.get("interp") or clip["time"]["interpolation"]


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def _bracket(times: list[float], t: float) -> tuple[int, int, float]:
    """(lo, hi, u) for explicit knot times, mirroring BakedWorldMotion.Sample."""

    last = len(times) - 1
    if t <= times[0]:
        return 0, 0, 0.0
    if t >= times[last]:
        return last, last, 0.0
    hi = bisect.bisect_right(times, t)
    lo = hi - 1
    span = max(1e-5, times[hi] - times[lo])
    return lo, hi, _clamp01((t - times[lo]) / span)


def sample_scalar(clip: dict[str, Any], channel: dict[str, Any], t: float,
                  fallback: float = 0.0) -> float:
    """Evaluate a stride-1 channel at ``t`` seconds (clamped into the clip)."""

    if channel is None:
        return fallback
    samples = channel["samples"]
    interp = channel_interp(clip, channel)
    time_block = clip["time"]
    times = time_block.get("times")
    if times is not None:
        lo, hi, u = _bracket(times, t)
        if interp == "step":
            return float(samples[lo])
        return float(samples[lo]) + (float(samples[hi]) - float(samples[lo])) * u

    fps = int(time_block["fps"])
    start_frame = int(time_block["startFrame"])
    count = int(time_block["sampleCount"])
    if interp == "step":
        step = math.floor((t * fps) + STEP_EPSILON)
        step = max(start_frame, min(start_frame + count - 1, step))
        return float(samples[step - start_frame])

    position = (t * fps) - start_frame
    position = max(0.0, min(float(count - 1), position))
    index = min(int(position), count - 2)
    frac = _clamp01(position - index)
    return float(samples[index]) + (float(samples[index + 1]) - float(samples[index])) * frac


def sample_quaternion(clip: dict[str, Any], channel: dict[str, Any],
                      t: float) -> tuple[float, float, float, float]:
    """Evaluate a stride-4 slerp channel at ``t`` seconds; returns (x, y, z, w)."""

    samples = channel["samples"]
    time_block = clip["time"]
    times = time_block.get("times")
    if times is not None:
        lo, hi, u = _bracket(times, t)
    else:
        fps = int(time_block["fps"])
        start_frame = int(time_block["startFrame"])
        count = int(time_block["sampleCount"])
        position = (t * fps) - start_frame
        position = max(0.0, min(float(count - 1), position))
        lo = min(int(position), count - 2)
        hi = lo + 1
        u = _clamp01(position - lo)
    qa = tuple(float(samples[(lo * 4) + i]) for i in range(4))
    qb = tuple(float(samples[(hi * 4) + i]) for i in range(4))
    return slerp(qa, qb, u)


def slerp(qa: tuple[float, float, float, float],
          qb: tuple[float, float, float, float],
          u: float) -> tuple[float, float, float, float]:
    """Shortest-arc spherical interpolation (nlerp below the small-angle knee).

    The C# mirror in ClipSampler.cs implements this exact algorithm; keep the
    two in lockstep (the 0.9995 knee included).
    """

    dot = (qa[0] * qb[0]) + (qa[1] * qb[1]) + (qa[2] * qb[2]) + (qa[3] * qb[3])
    if dot < 0.0:
        qb = (-qb[0], -qb[1], -qb[2], -qb[3])
        dot = -dot
    if dot > 0.9995:
        blended = tuple(qa[i] + ((qb[i] - qa[i]) * u) for i in range(4))
        norm = math.sqrt(sum(component * component for component in blended))
        if norm <= 0.0:
            return qa
        return tuple(component / norm for component in blended)  # type: ignore[return-value]
    theta = math.acos(max(-1.0, min(1.0, dot)))
    sin_theta = math.sin(theta)
    wa = math.sin((1.0 - u) * theta) / sin_theta
    wb = math.sin(u * theta) / sin_theta
    return tuple((qa[i] * wa) + (qb[i] * wb) for i in range(4))  # type: ignore[return-value]


# --------------------------------------------------------------------------- basis -> quaternion

def _normalize(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    magnitude = math.sqrt((vector[0] ** 2) + (vector[1] ** 2) + (vector[2] ** 2))
    if magnitude <= 1e-12:
        return (0.0, 0.0, 0.0)
    return (vector[0] / magnitude, vector[1] / magnitude, vector[2] / magnitude)


def _cross(a: tuple[float, float, float],
           b: tuple[float, float, float]) -> tuple[float, float, float]:
    return ((a[1] * b[2]) - (a[2] * b[1]),
            (a[2] * b[0]) - (a[0] * b[2]),
            (a[0] * b[1]) - (a[1] * b[0]))


def look_rotation(forward: tuple[float, float, float],
                  up: tuple[float, float, float]) -> tuple[float, float, float, float]:
    """Quaternion (x, y, z, w) matching UnityEngine.Quaternion.LookRotation semantics.

    Both the world-motion baker (which stores the quats) and the legacy differ
    reader (which mirrors the retired runtime LookRotation call) use THIS
    function, so knot rotations agree bit-for-bit on the python side.
    """

    z = _normalize(forward)
    if z == (0.0, 0.0, 0.0):
        return (0.0, 0.0, 0.0, 1.0)
    up_n = _normalize(up)
    if up_n == (0.0, 0.0, 0.0):
        up_n = (0.0, 1.0, 0.0)
    x = _normalize(_cross(up_n, z))
    if x == (0.0, 0.0, 0.0):
        # forward parallel to up: fall back to world up the way LookRotation does.
        x = _normalize(_cross((0.0, 1.0, 0.0), z))
        if x == (0.0, 0.0, 0.0):
            x = (1.0, 0.0, 0.0)
    y = _cross(z, x)

    m00, m01, m02 = x[0], y[0], z[0]
    m10, m11, m12 = x[1], y[1], z[1]
    m20, m21, m22 = x[2], y[2], z[2]
    trace = m00 + m11 + m22
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        return ((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, s / 4.0)
    if m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        return (s / 4.0, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    if m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        return ((m01 + m10) / s, s / 4.0, (m12 + m21) / s, (m02 - m20) / s)
    s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
    return ((m02 + m20) / s, (m12 + m21) / s, s / 4.0, (m10 - m01) / s)
