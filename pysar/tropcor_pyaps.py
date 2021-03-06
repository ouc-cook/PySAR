#!/usr/bin/env python3
############################################################
# Program is part of PySAR                                 #
# Copyright(c) 2015-2018, Heresh Fattahi, Zhang Yunjun     #
# Author:  Heresh Fattahi, Zhang Yunjun                    #
############################################################


import os
import sys
import re
import subprocess
try:
    import pyaps as pa
except ImportError:
    raise ImportError('Cannot import pyaps!')

import argparse
import h5py
import numpy as np
from pysar.objects import timeseries, geometry
from pysar.utils import readfile, writefile, ptime, utils as ut

standardWeatherModelNames = {'ERAI': 'ECMWF', 'ERAINT': 'ECMWF', 'ERAINTERIM': 'ECMWF',
                             'MERRA2': 'MERRA'
                             }


###############################################################
EXAMPLE = """example:
  tropcor_pyaps.py -d 20151002 20151003 --hour 12 -m ECMWF
  tropcor_pyaps.py -d date_list.txt     --hour 12 -m MERRA
  tropcor_pyaps.py -d 20151002 20151003 --hour 12 -m ECMWF -g geometryRadar.h5 --ref-yx 30 40
  tropcor_pyaps.py -f timeseries.h5 -g INPUTS/geometryRadar.h5 -m ECMWF
"""

REFERENCE = """reference:
  Jolivet, R., R. Grandin, C. Lasserre, M.-P. Doin and G. Peltzer (2011), Systematic InSAR tropospheric
  phase delay corrections from global meteorological reanalysis data, Geophys. Res. Lett., 38, L17311,
  doi:10.1029/2011GL048757

  Jolivet, R., P. S. Agram, N. Y. Lin, M. Simons, M. P. Doin, G. Peltzer, and Z. Li (2014), Improving
  InSAR geodesy using global atmospheric models, Journal of Geophysical Research: Solid Earth, 119(3),
  2324-2341.
"""

TEMPLATE = """
## 7. Tropospheric Delay Correction (optional and recommended)
## For pyaps method, correction is applied to dates with data available, and skipped for dates (usually recent) without it.
pysar.troposphericDelay.method       = auto  #[pyaps / height_correlation / base_trop_cor / no], auto for pyaps
pysar.troposphericDelay.weatherModel = auto  #[ERA / MERRA / NARR], auto for ECMWF, for pyaps method
pysar.troposphericDelay.weatherDir   = auto  #[path2directory], auto for "./../WEATHER"
"""

DATA_INFO = """
  re-analysis_dataset        coverage   temporal_resolution    spatial_resolution      latency     analysis
------------------------------------------------------------------------------------------------------------
ERA-Interim (by ECMWF)        Global      00/06/12/18 UTC      0.75 deg (~83 km)       2-month      4D-var
MERRA(2) (by NASA Goddard)    Global      00/06/12/18 UTC      0.5*0.625 (~50 km)     2-3 weeks     3D-var

To download MERRA2, you need an Earthdata account, and pre-authorize the "NASA GESDISC DATA ARCHIVE" application, following https://disc.gsfc.nasa.gov/earthdata-login.
"""

WEATHER_DIR = """--weather-dir ~/WEATHER
WEATHER/
├── ECMWF
│   ├── ERA-Int_20030329_06.grb
│   ├── ERA-Int_20030503_06.grb
└── MERRA
    ├── merra-20110126-06.nc4
    ├── merra-20110313-06.nc4
"""


def create_parser():
    parser = argparse.ArgumentParser(description='Tropospheric correction using weather models\n' +
                                     '  PyAPS is used to download and calculate the delay for each time-series epoch.',
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     epilog=REFERENCE+'\n'+DATA_INFO+'\n'+EXAMPLE)
    # For data download
    parser.add_argument('-m', '--model', '-s', dest='trop_model', default='ECMWF',
                        choices={'ECMWF', 'MERRA', 'NARR', 'ERA', 'MERRA1'},
                        help='source of the atmospheric data.\nNARR is working for 1979-Jan to 2014-Oct.')
    parser.add_argument('-d', '--date-list', dest='date_list', nargs='*',
                        help='Read the first column of text file as list of date to download data\n' +
                             'in YYYYMMDD or YYMMDD format')
    parser.add_argument('--hour', help='time of data in HH, e.g. 12, 06')
    parser.add_argument('-w', '--dir', '--weather-dir', dest='weather_dir',
                        help='parent directory of downloaded weather data file. Default: ./../WEATHER\n' +
                             'e.g.: '+WEATHER_DIR)

    # For delay calculation
    parser.add_argument('-g','--geomtry', dest='geom_file', type=str,
                        help='geometry file including height, incidenceAngle and/or latitude and longitude')
    parser.add_argument('--ref-yx', dest='ref_yx', type=int,
                        nargs=2, help='reference pixel in y/x')
    parser.add_argument('--delay', dest='delay_type', default='comb', choices={'comb', 'dry', 'wet'},
                        help='Delay type to calculate, comb contains both wet and dry delays')

    # For delay correction
    parser.add_argument('-f', '--file', dest='timeseries_file',
                        help='timeseries HDF5 file, i.e. timeseries.h5')
    parser.add_argument('-o', dest='outfile',
                        help='Output file name for trospheric corrected timeseries.')
    return parser


def cmd_line_parse(iargs=None):
    """Command line parser."""
    parser = create_parser()
    inps = parser.parse_args(args=iargs)

    if all(not i for i in [inps.date_list, inps.timeseries_file, inps.geom_file]):
        parser.print_help()
        sys.exit(1)
    return inps


###############################################################
def check_inputs(inps):
    parser = create_parser()
    atr = dict()
    if inps.timeseries_file:
        atr = readfile.read_attribute(inps.timeseries_file)
    elif inps.geom_file:
        atr = readfile.read_attribute(inps.geom_file)

    # Get Grib Source
    inps.trop_model = ut.standardize_trop_model(inps.trop_model,
                                                standardWeatherModelNames)
    print('weather model: '+inps.trop_model)

    # output file name
    if not inps.outfile:
        fbase = os.path.splitext(inps.timeseries_file)[0]
        inps.outfile = '{}_{}.h5'.format(fbase, inps.trop_model)

    # hour
    if not inps.hour:
        if 'CENTER_LINE_UTC' in atr.keys():
            inps.hour = ptime.closest_weather_product_time(atr['CENTER_LINE_UTC'],
                                                           inps.trop_model)
        else:
            parser.print_usage()
            raise Exception('no input for hour')
    print('time of cloest available product: {}:00 UTC'.format(inps.hour))

    # date list
    if inps.timeseries_file:
        print('read date list from timeseries file: {}'.format(inps.timeseries_file))
        ts_obj = timeseries(inps.timeseries_file)
        ts_obj.open(print_msg=False)
        inps.date_list = ts_obj.dateList
    elif len(inps.date_list) == 1:
        if os.path.isfile(inps.date_list[0]):
            print('read date list from text file: {}'.format(inps.date_list[0]))
            inps.date_list = ptime.yyyymmdd(np.loadtxt(inps.date_list[0],
                                                       dtype=bytes,
                                                       usecols=(0,)).astype(str).tolist())
        else:
            parser.print_usage()
            raise Exception('ERROR: input date list < 2')

    # weather directory
    if not inps.weather_dir:
        if inps.timeseries_file:
            inps.weather_dir = os.path.join(os.path.dirname(os.path.abspath(inps.timeseries_file)),
                                            '../WEATHER')
        elif inps.geom_file:
            inps.weather_dir = os.path.join(os.path.dirname(os.path.abspath(inps.geom_file)),
                                            '../WEATHER')
        else:
            inps.weather_dir = os.path.abspath(os.getcwd())
    print('weather data directory: '+inps.weather_dir)

    # Grib data directory
    inps.grib_dir = os.path.join(inps.weather_dir, inps.trop_model)
    if not os.path.isdir(inps.grib_dir):
        os.makedirs(inps.grib_dir)
        print('making directory: '+inps.grib_dir)

    # Date list to grib file list
    inps.grib_file_list = date_list2grib_file(inps.date_list,
                                              inps.hour,
                                              inps.trop_model,
                                              inps.grib_dir)

    if 'REF_Y' in atr.keys():
        inps.ref_yx = [int(atr['REF_Y']), int(atr['REF_X'])]
        print('reference pixel: {}'.format(inps.ref_yx))

    # Coordinate system: geocoded or not
    inps.geocoded = False
    if 'Y_FIRST' in atr.keys():
        inps.geocoded = True
    print('geocoded: {}'.format(inps.geocoded))

    # Prepare DEM, inc_angle, lat/lon file for PyAPS to read
    if inps.geom_file:
        geom_atr = readfile.read_attribute(inps.geom_file)
        print('converting DEM/incAngle for PyAPS to read')
        # DEM
        data = readfile.read(inps.geom_file, datasetName='height', print_msg=False)[0]
        inps.dem_file = 'pyapsDem.hgt'
        writefile.write(data, inps.dem_file, metadata=geom_atr)

        # inc_angle
        inps.inc_angle = readfile.read(inps.geom_file, datasetName='incidenceAngle', print_msg=False)[0]
        inps.inc_angle_file = 'pyapsIncAngle.flt'
        writefile.write(inps.inc_angle, inps.inc_angle_file, metadata=geom_atr)

        # latitude
        try:
            data = readfile.read(inps.geom_file, datasetName='latitude', print_msg=False)[0]
            print('converting lat for PyAPS to read')
            inps.lat_file = 'pyapsLat.flt'
            writefile.write(data, inps.lat_file, metadata=geom_atr)
        except:
            inps.lat_file = None

        # longitude
        try:
            data = readfile.read(inps.geom_file, datasetName='longitude', print_msg=False)[0]
            print('converting lon for PyAPS to read')
            inps.lon_file = 'pyapsLon.flt'
            writefile.write(data, inps.lon_file, metadata=geom_atr)
        except:
            inps.lon_file = None
    return inps, atr


###############################################################
def date_list2grib_file(date_list, hour, trop_model, grib_dir):
    grib_file_list = []
    for d in date_list:
        grib_file = grib_dir+'/'
        if   trop_model == 'ECMWF' :  grib_file += 'ERA-Int_%s_%s.grb' % (d, hour)
        elif trop_model == 'MERRA' :  grib_file += 'merra-%s-%s.nc4' % (d, hour)
        elif trop_model == 'NARR'  :  grib_file += 'narr-a_221_%s_%s00_000.grb' % (d, hour)
        elif trop_model == 'ERA'   :  grib_file += 'ERA_%s_%s.grb' % (d, hour)
        elif trop_model == 'MERRA1':  grib_file += 'merra-%s-%s.hdf' % (d, hour)
        grib_file_list.append(grib_file)
    return grib_file_list


def grib_file_name2trop_model_name(grib_file):
    grib_file = os.path.basename(grib_file)
    if grib_file.startswith('ERA-Int'):  trop_model = 'ECMWF'
    elif grib_file.startswith('merra'):  trop_model = 'MERRA'
    elif grib_file.startswith('narr'):   trop_model = 'NARR'
    elif grib_file.startswith('ERA_'):   trop_model = 'ERA'
    return trop_model


def check_exist_grib_file(gfile_list, print_msg=True):
    """Check input list of grib files, and return the existing ones with right size."""
    gfile_exist = ut.get_file_list(gfile_list)
    if gfile_exist:
        file_sizes = [os.path.getsize(i) for i in gfile_exist
                      if os.path.getsize(i) > 10e6]
        if file_sizes:
            comm_size = ut.most_common([i for i in file_sizes])
            if print_msg:
                print('common file size: {} bytes'.format(comm_size))
                print('number of grib files existed    : {}'.format(len(gfile_exist)))

            gfile_corrupt = []
            for gfile in gfile_exist:
                if os.path.getsize(gfile) < comm_size * 0.9:
                    gfile_corrupt.append(gfile)
        else:
            gfile_corrupt = gfile_exist

        if gfile_corrupt:
            if print_msg:
                print('------------------------------------------------------------------------------')
                print('corrupted grib files detected! Delete them and re-download...')
                print('number of grib files corrupted  : {}'.format(len(gfile_corrupt)))
            for i in gfile_corrupt:
                rmCmd = 'rm '+i
                print(rmCmd)
                os.system(rmCmd)
                gfile_exist.remove(i)
            if print_msg:
                print('------------------------------------------------------------------------------')
    return gfile_exist


def dload_grib_pyaps(grib_file_list):
    """Download weather re-analysis grib files using PyAPS
    Parameters: grib_file_list : list of string of grib files
    Returns:    grib_file_list : list of string
    """
    print('\n------------------------------------------------------------------------------')
    print('downloading weather model data using PyAPS ...')

    # Get date list to download (skip already downloaded files)
    grib_file_exist = check_exist_grib_file(grib_file_list, print_msg=True)
    grib_file2dload = sorted(list(set(grib_file_list) - set(grib_file_exist)))
    date_list2dload = [str(re.findall('\d{8}', i)[0]) for i in grib_file2dload]
    print('number of grib files to download: %d' % len(date_list2dload))
    print('------------------------------------------------------------------------------\n')

    # Download grib file using PyAPS
    if len(date_list2dload) > 0:
        hour = re.findall('\d{8}[-_]\d{2}', grib_file2dload[0])[0].replace('-', '_').split('_')[1]
        grib_dir = os.path.dirname(grib_file2dload[0])

        # try 3 times to download, then use whatever downloaded to calculate delay
        trop_model = grib_file_name2trop_model_name(grib_file2dload[0])
        i = 0
        while i < 3:
            i += 1
            try:
                if   trop_model == 'ECMWF' :  pa.ECMWFdload( date_list2dload, hour, grib_dir)
                elif trop_model == 'MERRA' :  pa.MERRAdload( date_list2dload, hour, grib_dir)
                elif trop_model == 'NARR'  :  pa.NARRdload(  date_list2dload, hour, grib_dir)
                elif trop_model == 'ERA'   :  pa.ERAdload(   date_list2dload, hour, grib_dir)
                elif trop_model == 'MERRA1':  pa.MERRA1dload(date_list2dload, hour, grib_dir)
            except:
                pass

    grib_file_list = check_exist_grib_file(grib_file_list, print_msg=False)
    return grib_file_list


def get_delay(grib_file, inps):
    """Get delay matrix using PyAPS for one acquisition
    Inputs:
        grib_file - strng, grib file path
        atr       - dict, including the following attributes:
                    dem_file    - string, DEM file path
                    trop_model - string, Weather re-analysis data source
                    delay_type  - string, comb/dry/wet
                    ref_y/x     - string, reference pixel row/col number
                    inc_angle   - np.array, 0/1/2 D
    Output:
        phs - 2D np.array, absolute tropospheric phase delay relative to ref_y/x
    """
    # initiate pyaps object
    if inps.geocoded:
        aps = pa.PyAPS_geo(grib_file, inps.dem_file, grib=inps.trop_model,
                           demtype=np.float32, demfmt='RMG',
                           verb=False, Del=inps.delay_type)
    else:
        aps = pa.PyAPS_rdr(grib_file, inps.dem_file, grib=inps.trop_model,
                           demtype=np.float32, demfmt='RMG',
                           verb=False, Del=inps.delay_type)

    # estimate delay
    phs = np.zeros((aps.ny, aps.nx), dtype=np.float32)
    if not inps.geocoded and inps.lat_file is not None:
        aps.getgeodelay(phs,
                        lat=inps.lat_file,
                        lon=inps.lon_file,
                        inc=inps.inc_angle_file)
    else:
        aps.getdelay(phs, inc=0.)
        phs /= np.cos(inps.inc_angle*np.pi/180.)

    # Get relative phase delay in space
    phs -= phs[inps.ref_yx[0], inps.ref_yx[1]]
    phs *= -1    # reverse the sign for consistency between different phase correction steps/methods
    return phs


def get_delay_timeseries(inps, atr):
    """Calculate delay time-series and write it to HDF5 file.
    Parameters: inps : namespace, all input parameters
                atr  : dict, metadata to be saved in trop_file
    Returns:    trop_file : str, file name of ECMWF.h5
    """
    if any(i is None for i in [inps.geom_file, inps.ref_yx]):
        print('No DEM / incidenceAngle / ref_yx found, exit.')
        return

    trop_file = os.path.join(os.path.dirname(inps.geom_file), inps.trop_model+'.h5')
    if ut.run_or_skip(out_file=trop_file, in_file=inps.grib_file_list, print_msg=False) == 'run':
        # calculate phase delay
        length, width = int(atr['LENGTH']), int(atr['WIDTH'])
        num_date = len(inps.grib_file_list)
        date_list = [str(re.findall('\d{8}', i)[0]) for i in inps.grib_file_list]
        trop_data = np.zeros((num_date, length, width), np.float32)

        print('calcualting delay for each date using PyAPS (Jolivet et al., 2011; 2014) ...')
        prog_bar = ptime.progressBar(maxValue=num_date)
        for i in range(num_date):
            grib_file = inps.grib_file_list[i]
            trop_data[i] = get_delay(grib_file, inps)
            prog_bar.update(i+1, suffix=os.path.basename(grib_file))
        prog_bar.close()

        # Convert relative phase delay on reference date
        try:
            inps.ref_date = atr['REF_DATE']
        except:
            inps.ref_date = date_list[0]
        print('convert to relative phase delay with reference date: '+inps.ref_date)
        inps.ref_idx = date_list.index(inps.ref_date)
        trop_data -= np.tile(trop_data[inps.ref_idx, :, :], (num_date, 1, 1))

        # Write tropospheric delay to HDF5
        ts_obj = timeseries(trop_file)
        ts_obj.write2hdf5(data=trop_data,
                          dates=date_list,
                          metadata=atr,
                          refFile=inps.timeseries_file)
    else:
        print('{} file exists and is newer than all GRIB files, skip updating.'.format(trop_file))

    # Delete temporary DEM file in ROI_PAC format
    temp_files =[fname for fname in [inps.dem_file,
                                     inps.inc_angle_file,
                                     inps.lat_file,
                                     inps.lon_file] 
                 if (fname is not None and 'pyaps' in fname)]
    if temp_files:
        print('delete temporary geometry files')
        rmCmd = 'rm '
        for fname in temp_files:
            rmCmd += ' {f} {f}.rsc '.format(f=fname)
        print(rmCmd)
        os.system(rmCmd)
    return trop_file


def correct_timeseries(timeseries_file, trop_file, out_file):
    print('\n------------------------------------------------------------------------------')
    print('correcting delay for input time-series by calling diff.py')
    cmd = 'diff.py {} {} -o {} --force'.format(timeseries_file,
                                               trop_file,
                                               out_file)
    print(cmd)
    status = subprocess.Popen(cmd, shell=True).wait()
    if status is not 0:
        raise Exception(('Error while correcting timeseries file '
                         'using diff.py with tropospheric delay file.'))
    return out_file


###############################################################
def main(iargs=None):
    inps = cmd_line_parse(iargs)
    inps, atr = check_inputs(inps)

    inps.grib_file_list = dload_grib_pyaps(inps.grib_file_list)

    trop_file = get_delay_timeseries(inps, atr)

    if atr['FILE_TYPE'] == 'timeseries':
        inps.outfile = correct_timeseries(inps.timeseries_file,
                                          trop_file,
                                          out_file=inps.outfile)

    return inps.outfile


###############################################################
if __name__ == '__main__':
    main()
