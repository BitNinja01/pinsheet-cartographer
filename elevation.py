"""Elevation data access and green contour computation."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from affine import Affine
from PIL import Image
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform as warp_transform
import requests

from .geometry import chaikin_smooth_open


def compute_contours(
    z: np.ndarray,
    levels: list[float],
    merge_dist: float = 20.0,
) -> dict[float, list[np.ndarray]]:
    """Extract contour polylines from a 2D elevation grid using skimage.

    Args:
        z: 2D array (ny, nx) of elevation values.
        levels: Contour z-values to extract.
        merge_dist: Ignored; kept for backward compatibility.

    Returns:
        {level: [polyline, ...]} where each polyline is an (N, 2) array
        of (x, y) coordinates in grid-cell space.
    """
    from skimage.measure import find_contours

    result: dict[float, list[np.ndarray]] = {}
    for level in levels:
        contours = find_contours(z, level)
        if contours:
            result[level] = [
                np.column_stack([c[:, 1], c[:, 0]]) for c in contours
            ]
    return result


def _gaussian_blur(z: np.ndarray, sigma: float = 0.7) -> np.ndarray:
    """Apply Gaussian blur to a 2D array using separable convolution.
    
    NaN values are filled with the array mean before blurring and
    restored afterward. Small sigma values (0.5–1.0) reduce 1‑cell
    DEM noise while preserving real elevation features.
    """
    if sigma <= 0 or z.size == 0:
        return z
    radius = int(3 * sigma + 0.5)
    if radius < 1:
        return z

    x = np.arange(-radius, radius + 1, dtype=float)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    kernel /= kernel.sum()

    mask = np.isnan(z)
    if mask.any():
        fill = np.nanmean(z)
        work = np.where(mask, fill, z)
    else:
        work = z

    # Pad with edge values, convolve, crop back to original shape
    padded = np.pad(work, pad_width=radius, mode="edge")
    blurred = np.apply_along_axis(
        lambda v: np.convolve(v, kernel, mode="valid"), axis=1,
        arr=padded,
    )
    blurred = np.apply_along_axis(
        lambda v: np.convolve(v, kernel, mode="valid"), axis=0,
        arr=blurred,
    )
    return np.where(mask, np.nan, blurred)


def _upsample_dem(
    z: np.ndarray,
    transform: Affine,
    factor: int = 4,
) -> tuple[np.ndarray, Affine]:
    """Upsample DEM grid by *factor* (bilinear) and return adjusted transform."""
    if factor <= 1:
        return z, transform
    ny, nx = z.shape
    fill = np.nanmean(z) if np.isnan(z).any() else 0.0
    z_filled = np.where(np.isnan(z), fill, z)
    z_filled = np.ascontiguousarray(z_filled.astype(np.float32))
    img = Image.fromarray(z_filled, mode="F")
    img_big = img.resize((nx * factor, ny * factor), Image.BILINEAR)
    z_big = np.array(img_big, dtype=np.float64).reshape(ny * factor, nx * factor)
    transform_big = Affine(
        transform.a / factor, transform.b / factor, transform.c,
        transform.d / factor, transform.e / factor, transform.f,
    )
    return z_big, transform_big


def _upsample_mask(mask: np.ndarray, factor: int = 4) -> np.ndarray:
    """Upsample boolean mask by *factor* (nearest‑neighbour)."""
    if factor <= 1:
        return mask
    ny, nx = mask.shape
    img = Image.fromarray((mask.astype(np.uint8) * 255), mode="L")
    img_big = img.resize((nx * factor, ny * factor), Image.NEAREST)
    return np.array(img_big, dtype=bool)


def get_course_dem(
    course_name: str,
    holes_geo: dict,
    status_callback: callable | None = None,
) -> Path | None:
    """Download/cache the 1m DEM covering all green bounding boxes.
    Returns path to cached GeoTIFF or None if unavailable."""
    from .data import get_dem_path

    cache_path = get_dem_path(course_name)
    if cache_path.exists():
        return cache_path

    bounds = _course_green_bounds(holes_geo)
    if bounds is None:
        return None

    url = _search_tnm(bounds)
    if url is None:
        return None

    if status_callback:
        status_callback("Downloading elevation data...")
    _download_file(url, cache_path, status_callback=status_callback)
    return cache_path if cache_path.exists() else None


def _course_green_bounds(holes_geo: dict) -> tuple[float, float, float, float] | None:
    """(min_lon, min_lat, max_lon, max_lat) across all green polygons.
    
    Data is stored as [lat, lon] per OSM convention.
    """
    min_lat, max_lat = 90.0, -90.0
    min_lon, max_lon = 180.0, -180.0
    found = False
    for geom in holes_geo.values():
        for ring in geom.get("green", []):
            for pt in ring:
                lat, lon = pt[0], pt[1]
                min_lat = min(min_lat, lat)
                max_lat = max(max_lat, lat)
                min_lon = min(min_lon, lon)
                max_lon = max(max_lon, lon)
                found = True
    return (min_lon, min_lat, max_lon, max_lat) if found else None


def _search_tnm(bounds: tuple[float, float, float, float]) -> str | None:
    """Query USGS TNM API for 1m DEM download URL covering the bounds.

    Prefers standard topographic DEMs over topobathy (TopoBathy) products,
    since topobathy tiles often have extensive NODATA areas on raised terrain.
    """
    min_lon, min_lat, max_lon, max_lat = bounds
    params = {
        "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "prodFormats": "GeoTIFF",
        "max": 200,
        "returnGeometry": "false",
        "outputFormat": "JSON",
    }
    try:
        resp = requests.get(
            "https://tnmaccess.nationalmap.gov/api/v1/products",
            params=params, timeout=30,
        )
        resp.raise_for_status()

        preferred: list[str] = []
        fallback: list[str] = []
        for item in resp.json().get("items", []):
            if "1 Meter" not in item.get("title", ""):
                continue
            for key in ("downloadURL", "url", "URL"):
                url = item.get(key)
                if url and url.lower().endswith(".tif"):
                    if "TopoBathy" in item.get("title", ""):
                        fallback.append(url)
                    else:
                        preferred.append(url)
                    break

        if preferred:
            return preferred[0]
        if fallback:
            return fallback[0]
        return None
    except requests.RequestException:
        return None


def _download_file(
    url: str,
    dest: Path,
    status_callback: callable | None = None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                if total and status_callback:
                    downloaded += len(chunk)
                    mb_done = downloaded / (1024 * 1024)
                    mb_total = total / (1024 * 1024)
                    pct = downloaded * 100 // total
                    status_callback(
                        f"Downloading elevation data: "
                        f"{mb_done:.1f} MB of {mb_total:.0f} MB ({pct}%)"
                    )
    except requests.RequestException:
        if dest.exists():
            dest.unlink()


def sample_green_elevation(
    green_ring: list[list[float]],
    dem_path: Path,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, Any] | None:
    """Extract elevation grid within a green polygon.

    Args:
        green_ring: List of [lon, lat] vertices.
        dem_path: Path to cached 1m GeoTIFF.

    Returns:
        (x_2d, y_2d, z_2d, win_transform) where x/y are 2D meshgrid arrays
        in DEM CRS coordinates, z is elevation, win_transform is the
        rasterio Affine for the window. Returns None on failure.
    """
    try:
        with rasterio.open(dem_path) as src:
            xs, ys = _ring_to_crs(green_ring, src.crs)
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)

            window = rasterio.windows.from_bounds(
                min_x, min_y, max_x, max_y, src.transform
            )
            z = src.read(1, window=window, masked=True)
            z_data = z.filled(np.nan)

            win_transform = src.window_transform(window)
            nx, ny = z_data.shape[1], z_data.shape[0]

            x_1d = np.linspace(
                win_transform.c,
                win_transform.c + win_transform.a * nx,
                nx,
            )
            y_1d = np.linspace(
                win_transform.f,
                win_transform.f + win_transform.e * ny,
                ny,
            )
            x_2d, y_2d = np.meshgrid(x_1d, y_1d, indexing="xy")
            return x_2d, y_2d, z_data, win_transform
    except Exception:
        return None


def _ring_to_crs(
    ring: list[list[float]], target_crs: CRS,
) -> tuple[list[float], list[float]]:
    """Convert ring from [lat, lon] (OSM convention) to target CRS coordinates.

    rasterio.warp.transform expects source coords as (xs, ys) = (lon, lat).
    """
    lats = [pt[0] for pt in ring]
    lons = [pt[1] for pt in ring]
    xs, ys = warp_transform(CRS.from_epsg(4326), target_crs, lons, lats)
    return list(xs), list(ys)



def _in_green_mask(
    x_2d: np.ndarray, y_2d: np.ndarray,
    z: np.ndarray,
    green_ring: list[list[float]],
    src_crs: CRS,
) -> np.ndarray | None:
    """Return boolean mask of DEM cells whose centres fall inside the green polygon."""
    from shapely import contains_xy
    from shapely.geometry import Polygon

    xs, ys = _ring_to_crs(green_ring, src_crs)
    green_poly = Polygon(list(zip(xs, ys)))

    valid = ~np.isnan(z)
    if not valid.any():
        return None

    x_flat = x_2d[valid]
    y_flat = y_2d[valid]
    contained = contains_xy(green_poly, x_flat, y_flat)

    mask = np.zeros_like(z, dtype=bool)
    mask[valid] = contained
    return mask if mask.any() else None


def compute_slot_contours(
    shading_img: Image.Image,
    svg_bx: float, svg_by: float,
    svg_bw: float, svg_bh: float,
    render_scale: float = 2.0,
    num_levels: int = 12,
) -> list[list[list[float]]]:
    """Extract contour paths from a grayscale elevation-shading image.

    Takes the PIL Image from compute_elevation_shading() and extracts
    contour paths in SVG slot pixel coordinates. Applies decimation,
    Chaikin smoothing, and length filtering.
    """
    img_contour = shading_img.resize(
        (max(1, int(svg_bw * render_scale)),
         max(1, int(svg_bh * render_scale))),
        Image.LANCZOS,
    )
    z_arr = np.array(img_contour, dtype=float)
    levels = [i * 255.0 / (num_levels + 1) for i in range(1, num_levels + 1)]
    raw_contours = compute_contours(z_arr, levels)
    contour_paths: list[list[list[float]]] = []
    for level in sorted(raw_contours):
        for polyline in raw_contours[level]:
            path = [[svg_bx + float(p[0]) / render_scale,
                     svg_by + float(p[1]) / render_scale]
                    for p in polyline]
            if len(path) >= 2:
                path_tuples = [(p[0], p[1]) for p in path]
                if len(path_tuples) >= 2 * 33:
                    decimated = path_tuples[::33]
                    if decimated[-1] != path_tuples[-1]:
                        decimated.append(path_tuples[-1])
                else:
                    decimated = path_tuples
                smoothed = chaikin_smooth_open(decimated, iterations=3)
                if len(smoothed) >= 2:
                    total_len = sum(
                        math.hypot(smoothed[i][0] - smoothed[i-1][0],
                                   smoothed[i][1] - smoothed[i-1][1])
                        for i in range(1, len(smoothed))
                    )
                    if total_len >= 30.0:
                        contour_paths.append([[x, y] for x, y in smoothed])
    return contour_paths


def compute_elevation_shading(
    green_ring: list[list[float]],
    dem_path: Path,
) -> Image.Image | None:
    """Compute a grayscale elevation-shading image for a green.

    Returns a PIL.Image (mode='L', uint8, 4x upscaled + blurred)
    or None if DEM data is insufficient or range < 0.25m.
    White = highest elevation, black = lowest.
    """
    sampled = sample_green_elevation(green_ring, dem_path)
    if sampled is None:
        return None

    x_2d, y_2d, z, win_transform = sampled

    if np.all(np.isnan(z)):
        return None

    with rasterio.open(dem_path) as src:
        src_crs = src.crs

    green_mask = _in_green_mask(x_2d, y_2d, z, green_ring, src_crs)
    if green_mask is not None:
        z_green = z[green_mask]
        z_min, z_max = np.nanmin(z_green), np.nanmax(z_green)
    else:
        return None

    if z_max - z_min < 0.10:
        return None

    z, _ = _upsample_dem(z, win_transform, factor=4)
    if green_mask is not None:
        green_mask_big = _upsample_mask(green_mask, factor=4)
    else:
        green_mask_big = None
    z = _gaussian_blur(z, sigma=1.5)

    if green_mask_big is not None:
        z_green_big = z[green_mask_big]
        z_min, z_max = np.nanmin(z_green_big), np.nanmax(z_green_big)

    z_norm = np.clip((z - z_min) / (z_max - z_min), 0.0, 1.0)
    z_uint8 = (z_norm * 255).astype(np.uint8)

    return Image.fromarray(z_uint8, mode="L")
