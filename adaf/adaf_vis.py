"""
ADAF - visualisations module

Creates visualisations from DEM and stores them as VRT.

DEMO VERSION: At the moment it only creates NORMALISED SLRM visualizations (min/max -0.5/0.5)

"""
import multiprocessing as mp
import os
import time
from math import ceil
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

import rvt.blend
import rvt.default
import rvt.vis
from rvt.blend_func import normalize_image
from vrt import build_vrt


def tiled_processing(
        input_vrt_path,
        ext_list,
        nr_processes=7,
        ll_dir=None
):
    # Start timer
    t0 = time.time()

    # This is the main dataset folder (where DEM file is located)
    output_dir_path = Path(input_vrt_path).parent

    # Get resolution of the dataset, because buffering is dependant on resolution!
    res = get_resolution(input_vrt_path)

    # DEFAULTS (for low-level visualizations)
    # ================================================
    # Default 1 (Slope, SLRM, MSTP, SVF, Openness +/-)
    default_1 = rvt.default.DefaultValues()
    # fill no_data and original no data
    default_1.fill_no_data = 1
    default_1.keep_original_no_data = 0
    # slope unchanged
    #
    # slrm  -  10 m (divide by pixel size!), can't be smaller than 10 pixels
    default_1.slrm_rad_cell = ceil(10 / res) if res < 1 else 10
    # svf  -  5 m (divide by pixel size)
    default_1.svf_compute = 0
    default_1.svf_r_max = ceil(5 / res)
    default_1.pos_opns_compute = 1
    default_1.neg_opns_compute = 1
    # MSTP (default values divided by resolution to get meters)
    default_1.mstp_local_scale = tuple(ceil(ti / res) for ti in default_1.mstp_local_scale)
    default_1.mstp_meso_scale = tuple(ceil(ti / res) for ti in default_1.mstp_meso_scale)
    default_1.mstp_broad_scale = tuple(ceil(ti / res) for ti in default_1.mstp_broad_scale)
    # Local Dominance - default values (nova vizualizacija)
    default_1.ld_min_rad = ceil(10 / res)
    default_1.ld_max_rad = ceil(20 / res)
    # default_1.ld_rad_inc = ceil(1 / res)
    # HILLSHADE FOR VAT General
    default_1.hs_sun_el = 35
    # ================================================
    # Default 2 (SVF & Openness for VAT)
    default_2 = rvt.default.DefaultValues()
    # fill no_data and original no data
    default_2.fill_no_data = 1
    default_2.keep_original_no_data = 0
    # svf
    default_2.svf_r_max = ceil(10 / res)  # 10 m (divide by pixel size)
    default_2.pos_opns_compute = 1
    default_2.neg_opns_compute = 0
    default_2.svf_noise = 3  # Remove noise High
    # HILLSHADE FOR VAT Flat
    default_1.hs_sun_el = 15

    # Prepare low-level dir
    # low_levels_dir = Path(low_levels_main_dir) / Path(input_vrt_path).parent.name
    if ll_dir:
        # low_levels_dir = Path(ll_drive + str(Path(input_vrt_path).parent)[1:])
        low_levels_dir = ll_dir
        low_levels_dir.mkdir(parents=True, exist_ok=True)
    else:
        low_levels_dir = output_dir_path

    # Prepare for multiprocessing
    const_params = [
        default_1,           # const 1
        default_2,           # const 2
        input_vrt_path,      # const 3
        low_levels_dir       # const 4
    ]

    # Get basename of VRT file, required for building output name
    input_process_list = []
    # TODO, extents are calculated HERE! Or add a function that does that (not required at input!)
    ext_list1 = ext_list[["minx", "miny", "maxx", "maxy"]].values.tolist()
    for i, input_dem_extents in enumerate(ext_list1):
        # --> USE THIS IF YOU WANT TO ADD INDEX TO FILENAME
        # # tile_id = ext_list.tile_ID.iloc[i]
        # # out_name = f"{left:.0f}_{bottom:.0f}_rvt_id-{tile_id}.tif"
        #

        # Prepare file name as left-bottom coordinates
        left = ext_list.minx.iloc[i]
        bottom = ext_list.miny.iloc[i]
        out_name = f"{left:.0f}_{bottom:.0f}_rvt.tif"

        # general_out_path = os.path.abspath(os.path.join(output_dir_path, out_name))

        # Append variable parameters to the list for multiprocessing
        to_append = const_params.copy()  # Copy the constant parameters
        # Append variable parameters
        to_append.append(input_dem_extents)  # var 1
        to_append.append(out_name)  # var 2
        to_append.append(i)  # var 3
        # Change list to tuple
        input_process_list.append(tuple(to_append))

    # # DEBUG: RUN SINGLE INSTANCE
    # one_instance = input_process_list[13]
    # res = compute_save_low_levels(*one_instance)
    # print(res)

    # multiprocessing
    skipped_tiles = []
    with mp.Pool(nr_processes) as p:
        realist = [p.apply_async(compute_save_low_levels, r) for r in input_process_list]
        for result in realist:
            pool_out = result.get()
            # Check if tile was all NaN's (remove it from REFGRID!)
            if pool_out[0] == 1:
                print("Skipped (tile_ID:", pool_out[1], ");", pool_out[2])
                skipped_tiles.append(pool_out[1])
            else:
                print("tile_ID:", pool_out[1], ";", pool_out[2])

    # # Remove tiles from REFGRID if any (that was the case in Noise mapping)
    # if skipped_tiles:
    #     ext_list = ext_list[~ext_list["tile_ID"].isin(skipped_tiles)]
    #     refg_pth = list(output_dir_path.glob("*_refgrid*"))[0]  # Find path to "refgrid" file
    #     ext_list.to_file(refg_pth, driver="GPKG")

    # Prepare list with all output tiles paths
    all_tiles_paths = [pth[3].as_posix() for pth in input_process_list]

    # Build VRTs
    # TODO: hardcoded for slrm, change if different vis will be available
    ds_dir = low_levels_dir / 'slrm'
    vrt_name = Path(input_vrt_path).stem + "_" + Path(ds_dir).name + ".vrt"
    out_path = build_vrt(ds_dir, vrt_name)
    print("  - Created:", out_path)

    t1 = time.time() - t0
    print(f"Done with computing low-level visualizations in {round(t1/60, ndigits=None)} min.")

    return {"output_directory": ds_dir, "files_list": all_tiles_paths}


# function which is multiprocessing
def compute_save_low_levels(
        default_1,
        default_2,
        vrt_path,
        low_levels_dir,
        input_dem_extents,
        dem_name,
        tile_id
):
    # **************** TAKE THIS OUT OF THE FUNCTION **************

    # Read buffer values from defaults (already changed from meters to pixels!!!)!
    #  TODO: select visualizations at input (see TODO in Build VRTs! LN 147)
    # buffer_dict = {
    #     "slrm": default_1.slrm_rad_cell,
    #     "svf": default_1.svf_r_max,  # SVF, OPNS+ and OPNS-
    #     "svf_VAT": default_2.svf_r_max,  # SVF and OPNS+ for VAT
    #     "slope": 0,
    #     "mstp": default_1.mstp_broad_scale[1],
    #     "svf_1": default_1.svf_r_max,
    #     "ld": default_1.ld_max_rad,
    #     "hs_general": 1,
    #     "hs_flat": 1
    # }
    buffer_dict = {
        "slrm": default_1.slrm_rad_cell
    }

    # *******************************

    # Select the largest required buffer
    max_buff = max(buffer_dict, key=buffer_dict.get)
    buffer = buffer_dict[max_buff]

    # Read array into RVT dictionary format
    dict_arrays = get_raster_vrt(vrt_path, input_dem_extents, buffer)

    # Add default path
    dict_arrays["default_path"] = dem_name

    # Change nodata value to np.nan, to avoid problems later
    dict_arrays["array"][dict_arrays["array"] == dict_arrays["no_data"]] = np.nan
    dict_arrays["no_data"] = np.nan

    # Then check, if output slice (w/o buffer) is all NaNs, then skip this tile if yes
    if (dict_arrays["array"][buffer:-buffer, buffer:-buffer] == np.nan).all():
        # If all NaNs encountered, output the tile ID
        return 1, tile_id, f"Skipping, all NaNs in: {dem_name}"

    # --- START VISUALIZATION WITH RVT ---

    for vis_type in buffer_dict:
        # Obtain buffer for current visualization type
        arr_buff = buffer_dict[vis_type]
        # Slice raster to minimum required size
        arr_slice = buffer_dict[max_buff] - arr_buff
        if arr_slice == 0:
            sliced_arr = dict_arrays["array"]
        else:
            sliced_arr = dict_arrays["array"][arr_slice:-arr_slice, arr_slice:-arr_slice]

        # Run visualization
        if vis_type == "slrm":
            slrm = default_1.get_slrm(sliced_arr)
            out_slrm = normalize_image(
                visualization="slrm",
                image=slrm.squeeze(),
                min_norm=-0.5,
                max_norm=0.5,
                normalization="value"
            )
            out_slrm[np.isnan(out_slrm)] = 0
            vis_out = {
                vis_type: out_slrm
            }
            # Determine output name
            vis_paths = {
                vis_type: low_levels_dir / vis_type / default_1.get_slrm_file_name(dem_name)
            }
        elif vis_type == "slope":
            vis_out = {
                vis_type: default_1.get_slope(
                    sliced_arr,
                    resolution_x=dict_arrays["resolution"][0],
                    resolution_y=dict_arrays["resolution"][1]
                )
            }
            vis_paths = {
                vis_type: low_levels_dir / vis_type / default_1.get_slope_file_name(dem_name)
            }
        elif vis_type == "mstp":
            vis_out = {
                vis_type: default_1.get_mstp(sliced_arr)
            }
            vis_paths = {
                vis_type: low_levels_dir / vis_type / default_1.get_mstp_file_name(dem_name)
            }
        elif vis_type == "svf":
            vis_out = default_1.get_sky_view_factor(
                sliced_arr,
                dict_arrays["resolution"][0],
                compute_svf=False,
                compute_opns=True
            )
            vis_out["neg_opns"] = default_1.get_neg_opns(
                sliced_arr,
                dict_arrays["resolution"][0]
            )
            r = default_1.svf_r_max  # Find radius for name - for example R5
            vis_paths = {
                # "svf": os.path.join(low_levels_dir, f"svf_R{r}", default_1.get_svf_file_name(dem_path)),
                "opns": low_levels_dir / f"opns_R{r}" / default_1.get_opns_file_name(dem_name),
                "neg_opns": low_levels_dir / f"neg_opns_R{r}" / default_1.get_neg_opns_file_name(dem_name)
            }
        elif vis_type == "svf_VAT":
            vis_out = default_2.get_sky_view_factor(
                sliced_arr,
                dict_arrays["resolution"][0],
                compute_opns=True
            )
            r = default_2.svf_r_max  # Find radius for name - for example R5
            vis_paths = {
                "svf": low_levels_dir / f"svf_R{r}" / default_2.get_svf_file_name(dem_name),
                "opns": low_levels_dir / f"opns_R{r}" / default_2.get_opns_file_name(dem_name)
            }
        elif vis_type == "svf_1":
            vis_out = default_1.get_sky_view_factor(
                sliced_arr,
                dict_arrays["resolution"][0],
                compute_svf=True,
                compute_opns=False
            )
            r = default_1.svf_r_max
            vis_paths = {
                "svf": os.path.join(low_levels_dir, f"svf_R{r}", default_1.get_svf_file_name(dem_name))
            }
        elif vis_type == "ld":
            vis_out = {
                vis_type: default_1.get_local_dominance(sliced_arr)
            }
            vis_paths = {
                vis_type: low_levels_dir / vis_type / default_1.get_local_dominance_file_name(dem_name)
            }
        elif vis_type == "mstp_2":
            vis_out = {
                vis_type: default_2.get_mstp(sliced_arr)
            }
            vis_paths = {
                vis_type: low_levels_dir / vis_type / default_2.get_mstp_file_name(dem_name)
            }
        elif vis_type == "hs_general":
            vis_out = {
                vis_type: default_1.get_hillshade(
                    sliced_arr,
                    resolution_x=dict_arrays["resolution"][0],
                    resolution_y=dict_arrays["resolution"][1]
                )
            }
            vis_paths = {
                vis_type: low_levels_dir / vis_type / default_1.get_hillshade_file_name(dem_name)
            }
        elif vis_type == "hs_flat":
            vis_out = {
                vis_type: default_2.get_hillshade(
                    sliced_arr,
                    resolution_x=dict_arrays["resolution"][0],
                    resolution_y=dict_arrays["resolution"][1]
                )
            }
            vis_paths = {
                vis_type: low_levels_dir / vis_type / default_2.get_hillshade_file_name(dem_name)
            }
        else:
            raise ValueError("Wrong vis_type in the visualization for loop")

        # Save visualization to file
        for i in vis_out:
            # Slice away buffer
            if arr_buff == 0:
                arr_out = vis_out[i]
            else:
                arr_out = vis_out[i][..., arr_buff:-arr_buff, arr_buff:-arr_buff]
            # Make sure the dimensions of array are correct
            if arr_out.ndim == 2:
                arr_out = np.expand_dims(arr_out, axis=0)
            # Determine output name
            arr_save_path = vis_paths[i]
            os.makedirs(os.path.dirname(arr_save_path), exist_ok=True)

            # # Add to results dictionary
            # vis_name = os.path.basename(os.path.dirname(arr_save_path))
            # dict_arrays[vis_name] = arr_out

            # Save using rasterio
            out_profile = dict_arrays["profile"].copy()
            out_profile.update(dtype=arr_out.dtype,
                               count=arr_out.shape[0],
                               nodata=0)  # TODO: was NaN, use 0 for SLRM in ADAF
            with rasterio.open(arr_save_path, "w", **out_profile) as dst:
                dst.write(arr_out)

    # print(dict_arrays)
    # # Release memory from variables that we don't need anymore
    # vis_out = None
    # arr_out = None
    # sliced_arr = None

    # CREATE BLENDS
    # e2_pth = rrim_e2mstp(dict_arrays)
    # vat_pth = vat_3bands(dict_arrays)
    # e3_pth = crim_e3mstp(dict_arrays)

    return 0, tile_id, f"Finished processing: {dem_name}"


def get_raster_vrt(vrt_path, extents, buffer):
    """
    Extents have to be transformed into rasterio Window object, it is passed into the function as tuple.
    (left, bottom, right, top)


    Parameters
    ----------
    vrt_path : str
        Path to raster file. Can be any rasterio readable format.
    extents : tuple
        Extents to be read (left, bottom, right, top).
    buffer : int
        Buffer in pixels.

    Returns
    -------
        A dictionary containing the raster array and all the required metadata.

    """
    with rasterio.open(vrt_path) as vrt:
        # Read VRT metadata
        vrt_res = vrt.res
        vrt_nodata = vrt.nodata
        vrt_transform = vrt.transform
        vrt_crs = vrt.crs

        # ADD BUFFER TO EXTENTS (LBRT) - transform pixels to meters!
        buffer_m = buffer * vrt_res[0]
        buff_extents = (
            extents[0] - buffer_m,
            extents[1] - buffer_m,
            extents[2] + buffer_m,
            extents[3] + buffer_m
        )

        # Pack extents into rasterio's Window object
        buff_window = from_bounds(*buff_extents, vrt_transform)
        orig_window = from_bounds(*extents, vrt_transform)

        # Read windowed array (with added buffer)
        # boundless - if window falls out of bounds, read it and fill with NaNs
        win_array = vrt.read(window=buff_window, boundless=True)

        # Save transform object of both extents (original and buffered)
        buff_transform = vrt.window_transform(buff_window)
        orig_transform = vrt.window_transform(orig_window)

    # For raster with only one band, remove first axis from the array (RVT requirement)
    if win_array.shape[0] == 1:
        win_array = np.squeeze(win_array, axis=0)

    # Prepare output metadata profile
    out_profile = {
        'driver': 'GTiff',
        'nodata': None,
        'width':  win_array.shape[1] - 2 * buffer,
        'height':  win_array.shape[0] - 2 * buffer,
        'count':  1,
        'crs': vrt_crs,
        'transform': orig_transform,
        "compress": "lzw"
    }

    output = {
        "array": win_array,
        "resolution": vrt_res,
        "no_data": vrt_nodata,
        "buff_transform": buff_transform,
        "orig_transform": orig_transform,
        "crs": vrt_crs,
        "profile": out_profile
    }

    return output


def get_resolution(path):
    with rasterio.open(path) as src:
        resolution = src.res[0]

    return resolution

