"""시장 데이터 스토어 — as_of(t) 뷰만 노출해 look-ahead를 구조로 차단 (IMPL-05, BT-D3).

시뮬레이터가 시점 t의 전략에 주입하는 AsOfView는 t 이후 인덱스가 존재하지 않는
복사 슬라이스다 — 미래를 감춘 게 아니라 뷰 안에 없다. 데이터 소스는 CSV 픽스처
주입(Phase 1, 어댑터 불요): 컬럼 date,symbol,close[,traded_value].
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np


class DataError(ValueError):
    """픽스처 형식 오류 — 정렬·정합 실패는 조용히 보정하지 않고 거부한다."""


class AsOfView:
    """시점 t까지만 존재하는 읽기 전용 데이터 뷰. 전략 슬롯의 유일한 데이터 입력."""

    __slots__ = ("_dates", "_closes", "_traded")

    def __init__(
        self,
        dates: tuple[str, ...],
        closes: dict[str, np.ndarray],
        traded: dict[str, np.ndarray],
    ) -> None:
        self._dates = dates
        self._closes = closes
        self._traded = traded

    @property
    def dates(self) -> tuple[str, ...]:
        return self._dates

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._closes))

    def __len__(self) -> int:
        return len(self._dates)

    def close(self, symbol: str) -> np.ndarray:
        return self._closes[symbol]

    def traded_value(self, symbol: str) -> np.ndarray:
        return self._traded[symbol]


class MarketDataStore:
    """전 종목이 동일 날짜 격자에 정렬된 종가 스토어. 자유 접근 배열을 노출하지 않는다."""

    def __init__(
        self,
        dates: list[str],
        closes: dict[str, np.ndarray],
        traded: dict[str, np.ndarray],
    ) -> None:
        if dates != sorted(dates):
            raise DataError("날짜가 오름차순이 아니다")
        if len(set(dates)) != len(dates):
            raise DataError("중복 날짜가 있다")
        n = len(dates)
        for sym, arr in closes.items():
            if len(arr) != n or len(traded[sym]) != n:
                raise DataError(f"{sym}: 날짜 격자와 길이가 다르다")
            if not np.all(np.isfinite(arr)) or np.any(np.asarray(arr) <= 0):
                raise DataError(f"{sym}: 종가에 비정상 값이 있다")
        self._dates = list(dates)
        self._closes = {s: np.asarray(a, dtype=float) for s, a in closes.items()}
        self._traded = {s: np.asarray(traded[s], dtype=float) for s in closes}
        self._index = {d: i for i, d in enumerate(dates)}

    @classmethod
    def from_csv(cls, path: str | Path) -> "MarketDataStore":
        rows: dict[str, dict[str, tuple[float, float]]] = {}
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            required = {"date", "symbol", "close"}
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise DataError(f"CSV 헤더에 {sorted(required)} 필요: {reader.fieldnames}")
            has_tv = "traded_value" in (reader.fieldnames or [])
            for r in reader:
                tv = float(r["traded_value"]) if has_tv and r["traded_value"] else 0.0
                rows.setdefault(r["symbol"], {})[r["date"]] = (float(r["close"]), tv)
        if not rows:
            raise DataError("빈 CSV")
        dates = sorted(set().union(*(d.keys() for d in rows.values())))
        closes, traded = {}, {}
        for sym, by_date in rows.items():
            missing = [d for d in dates if d not in by_date]
            if missing:
                raise DataError(f"{sym}: 결측 날짜 {missing[:3]}... — 격자 정합 실패")
            closes[sym] = np.array([by_date[d][0] for d in dates])
            traded[sym] = np.array([by_date[d][1] for d in dates])
        return cls(dates, closes, traded)

    @property
    def dates(self) -> tuple[str, ...]:
        return tuple(self._dates)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._closes))

    def __len__(self) -> int:
        return len(self._dates)

    def index_of(self, date: str) -> int:
        if date not in self._index:
            raise DataError(f"스토어에 없는 날짜: {date}")
        return self._index[date]

    def date(self, i: int) -> str:
        return self._dates[i]

    def close_at(self, symbol: str, i: int) -> float:
        """시뮬레이터 전용 단일 시점 조회 (체결가). 전략에는 절대 주입하지 않는다."""
        return float(self._closes[symbol][i])

    def as_of(self, i: int) -> AsOfView:
        """인덱스 i(포함)까지의 뷰. i 이후는 뷰에 존재하지 않는다 (BT-D3)."""
        if not (0 <= i < len(self._dates)):
            raise DataError(f"범위 밖 인덱스: {i}")
        closes: dict[str, np.ndarray] = {}
        traded: dict[str, np.ndarray] = {}
        for sym in self._closes:
            c = self._closes[sym][: i + 1].copy()
            t = self._traded[sym][: i + 1].copy()
            c.setflags(write=False)
            t.setflags(write=False)
            closes[sym] = c
            traded[sym] = t
        return AsOfView(tuple(self._dates[: i + 1]), closes, traded)
