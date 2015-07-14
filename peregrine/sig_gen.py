#!/usr/bin/env python
from datetime import datetime, timedelta
import argparse
import dateutil.parser
import numpy as np
import scipy.optimize as opt
import sys

from peregrine.gps_time import datetime_to_tow
import peregrine.gps_constants as gps
import peregrine.ephemeris as eph
import peregrine.initSettings
import peregrine.include.generateCAcode as gencode
from peregrine.short_set import sagnac
import peregrine.parallel_processing as pp
import peregrine.samples
import peregrine.warm_start
import swiftnav.coord_system as coord

def integrate_state(x0, dt):
    ''' Given a state (position, velocity, acceleration, jerk)
    integrate to find a new state at some time later.
    '''
    x1 = [x0[0] + dt * x0[1] + dt**2 * x0[2] / 2 + dt**3 * x0[3] / 6,
          x0[1] + dt * x0[2] + dt**2 * x0[3] / 2,
          x0[2] + dt * x0[3],
          x0[3]]
    return np.array(x1)

def pvaj(x):
    ''' Extract the XYZ position, velocity and jerk from a trajectory row '''
    return np.reshape(x[1:13], [4, 3])

def mags(x):
    ''' Get the magnitudes of the XYZ position, velocity and jerk '''
    return np.linalg.norm(x, axis=1)

def smoothify(traj0, traj1):
    '''Given two trajectory rows [time, pos, vel, acc, jerk], return more
    rows to be inserted between them such that the resulting trajectory
    is smooth in position, velocity and acceleration.
    '''
    # We'll choose three points at times equally spaced between the boundary conditions
    n_new_pts = 3

    def fit3(t_event, t_ramp):
        traj = np.vstack([traj0, np.zeros((n_new_pts, 13)), traj1])
        # Choose the times
        traj[1, 0] = t_event - t_ramp
        traj[2, 0] = t_event
        traj[3, 0] = t_event + t_ramp
        dt = np.diff(traj[:, 0])
        # Transition from one point to the next via kinematic integration:
        # x1 = Ax0 + Bj0
        # x2 = Ax1 + Bj1
        #    = AAx0 + ABj0 + Bj1
        # ...
        # x4 = AAAAx0 + AAABj0 + AABj1 + ABj2 + Bj3
        # where x is the pos/vel/acc state, j is the chosen jerk

        A = lambda dt: np.array([[1, dt, dt**2 / 2],
                                 [0, 1, dt],
                                 [0, 0, 1]])
        B = lambda dt: np.array([dt**3 / 6,
                                 dt**2 / 2,
                                 dt])
        # Solving for j1, j2, j3:
        #   x4 - AAAAx0 - AAABj0 = AABj1 + ABj2 + Bj3
        # or
        #   Q = RJ
        # where
        # J = [j1, j2, j3]

        # print "traj0: ", traj0
        # print "pvaj(traj0): ", pvaj(traj0)
        for spatial_axis in range(3):
            # print "%s axis" % ("XYZ"[spatial_axis])
            x0 = pvaj(traj[0])[0:3,spatial_axis]
            # print "x0 = " + str(x0)
            x4 = pvaj(traj[4])[0:3,spatial_axis]
            # print "x4 = " + str(x4)
            j0 = pvaj(traj[0])[3,spatial_axis]
            # print "j0 = ", j0
            Q = x4 - A(dt[3]).dot(A(dt[2])).dot(A(dt[1])).dot(A(dt[0])).dot(x0) - A(dt[3]).dot(A(dt[2])).dot(A(dt[1])).dot(B(dt[0])) * j0
            R = np.array([
                A(dt[3]).dot(A(dt[2])).dot(B(dt[1])),
                A(dt[3]).dot(B(dt[2])),
                B(dt[3])]).T
            # print "R = ", R
            J = np.linalg.inv(R).dot(Q)
            # print "J = ", J
            x1 = A(dt[0]).dot(x0) + B(dt[0]) * j0
            x2 = A(dt[1]).dot(x1) + B(dt[1]) * J[0]
            x3 = A(dt[2]).dot(x2) + B(dt[2]) * J[1]
            x4_p = A(dt[3]).dot(x3) + B(dt[3]) * J[2]

#            print "LHS = ", Q
#            print "RHS = ", A.dot(A).dot(B).dot(J[0]) + A.dot(B).dot(J[1]) + B.dot(J[2])
#            print "RJ = ", R.dot(J)
            # print "x4_p - x4:" , x4_p - x4

            if np.linalg.norm(x4_p - x4) > 1e-5:
                raise ValueError("Residual too large")
            
            traj[1][1 + 3 * 0 + spatial_axis] = x1[0]
            traj[1][1 + 3 * 1 + spatial_axis] = x1[1]
            traj[1][1 + 3 * 2 + spatial_axis] = x1[2]
            traj[1][1 + 3 * 3 + spatial_axis] = J[0]
            
            traj[2][1 + 3 * 0 + spatial_axis] = x2[0]
            traj[2][1 + 3 * 1 + spatial_axis] = x2[1]
            traj[2][1 + 3 * 2 + spatial_axis] = x2[2]
            traj[2][1 + 3 * 3 + spatial_axis] = J[1]
            
            traj[3][1 + 3 * 0 + spatial_axis] = x3[0]
            traj[3][1 + 3 * 1 + spatial_axis] = x3[1]
            traj[3][1 + 3 * 2 + spatial_axis] = x3[2]
            traj[3][1 + 3 * 3 + spatial_axis] = J[2]

        return traj

    DT = traj1[0] - traj0[0]
    t_min = 1e-5

    if 0:
        def cost(x):
            print "Cost(%.12f, %.12f)" % (x[0], x[1])
            t_event = traj0[0] + DT * x[0]
            t_ramp = DT * x[1]
            traj = fit3(t_event, t_ramp)
            excess_accel = (traj[1:4,:] - traj[0,:])[:, 7:10]  # 3x3
            c = np.sum(excess_accel ** 2)
            print " = ", c
            return c

        x_guess = [0.5, 0.25]
        cons = ({'type': 'ineq', 'fun': lambda x:  x[0] - x[1]},    # Lower bound
                {'type': 'ineq', 'fun': lambda x:  1-(x[0] + x[1])}) # Upper bound
        bnds = [(2*t_min, 1-2*t_min), (t_min, 0.5-t_min)]
    else:
        t_ramp = 1e-2 * DT
        def cost(x):
            print "Cost(%.12f)" % (x[0])
            t_event = traj0[0] + DT * x[0]
            traj = fit3(t_event, t_ramp)
    #        print traj
            excess_accel = (traj[1:4,:] - traj[0,:])[:, 7:10]  # 3x3
            print 'Traj:'
            print traj
            print 'Excess accel:'
            print excess_accel
            c = np.sum(excess_accel ** 2)
            print "c = ", c
            return c
        x_guess = [0.5]
        bnds = [(t_min + t_ramp / DT, 1-(t_min + t_ramp/DT))]
        cons = []
        
    result = opt.minimize(cost, x_guess, bounds=bnds, constraints=cons, options={'disp': True})
    print result
    t_event = traj0[0] + DT * result.x[0]
#    t_ramp = DT * result.x[1]
    traj = fit3(t_event, t_ramp)
    
    print 
    print traj[0]
    print traj[1]
    print traj[2]
    print traj[3]
    print traj[4]
    print
#    raise ValueError("how does this look?")
    return [traj[1], traj[2], traj[3]]

def check_smooth(traj, tol=[0.010, 0.1, 10], repair=False, verbose=True):
    '''Check that the position, velocity and acceleration terms
    in a trajectory are smooth to within some tolerance.
    '''
    smooth = True
    traj_new = [traj[0]]
    for i in range(1, len(traj)):
        this_pt = traj[i]
        x0 = pvaj(traj[i-1])
        dt = this_pt[0] - traj[i-1][0]
        x1 = integrate_state(x0, dt)
        dx = pvaj(this_pt) - x1
        new_pt = None
        for axis, desc in enumerate(["Position", "Velocity", "Acceleration"]):
            if np.any(np.abs(dx[axis]) > tol[axis]):
                if verbose:
                    print "%s discontinuity at row %d, time %.3f:" % (
                        desc, i, this_pt[0])
                    print dx[axis]
                smooth = False
                if repair:
                    traj_new += smoothify(traj[i-1], this_pt)
        traj_new.append(this_pt)
    return np.array(traj_new), smooth

def load_umt(filename):
    x = np.loadtxt(filename, comments='<', delimiter=',', ndmin=2,
                   usecols=(0,         # Time
                            3,4,5,     # Position
                            6,7,8,     # Velocity
                            9,10,11,   # Acceleration
                            12,13,14)) # Jerk
    return x

def plot_traj(traj):
    import matplotlib.pyplot as plt
    tt = traj # - traj[0]
    m = np.array(map(mags, map(pvaj, tt)))
    t = tt[:,0]
    x = m[:,0]
    v = m[:,1]
    a = m[:,2]
    j = m[:,3]

    fig = plt.figure(figsize=[12,12])
    ax=fig.add_subplot(211)
    ax.plot(t,x,'-b')
    ax.set_ylabel('Position', color='b')
    for tl in ax.get_yticklabels():
        tl.set_color('b')
    ax2 = ax.twinx()
    ax2.plot(t,v,'-r')
    ax2.set_ylabel('Velocity', color='r')
    for tl in ax2.get_yticklabels():
        tl.set_color('r')

    ax=fig.add_subplot(212)
    ax.plot(t,a,'-b')
    ax.set_ylabel('Acceleration', color='b')
    for tl in ax.get_yticklabels():
        tl.set_color('b')
    ax2 = ax.twinx()
    ax2.plot(t,j,'-r')
    ax2.set_ylabel('Jerk', color='r')
    for tl in ax2.get_yticklabels():
        tl.set_color('r')

def interp_pv(traj, t):
    # Find the entry in the trajectory table just preceding t
    idx = max(0, np.argmax(traj[:,0] > t) - 1)
    x0 = pvaj(traj[idx])
    # Extrapolate from there
    dt = t - traj[idx][0]
    return integrate_state(x0, dt)[0:2]
        
def interp_traj(traj, t0, t_run, t_skip=0, t_step=0.002, fs=16.368e6):
    tow0 = datetime_to_tow(t0)[1]
    t_start = traj[0][0] + t_skip
    t_stop = t_start + t_run
    step_samps = int(fs * t_step)
    step_dt = step_samps/fs
    step_samp_ix = np.arange(t_run / step_dt) * step_samps
    step_t = t_start + step_samp_ix / fs
    step_tow = tow0 + step_t
    step_pv = map(lambda t: interp_pv(traj, t), step_t)

    return step_t, step_tow, step_pv, step_samps

def gen_signal_sat_los(t0, x0, v, n_samples, fs, fi, cacode, nav_msg, nav_msg_tow0, jitter=0):
    t = t0 + np.arange(n_samples) / fs # + np.random.normal(size=n_samples)*jitter
    x = x0 + (t-t0) * v
    carrier_phase = 2 * gps.pi * ((fi * t) - (x / (gps.c / gps.l1)))
    code_phase = np.asarray((t * gps.chip_rate) - x / (gps.c / gps.chip_rate), np.int64)
    code_ixs = code_phase % gps.chips_per_code
    if nav_msg is None:
        nav_msg = np.array([1])
    nav_msg_ixs = (code_phase / (20*gps.chips_per_code) - 50 * np.int64(nav_msg_tow0)) % len(nav_msg)
#    print "step: t0 =", t0, " nav_msg_ixs[0] =", nav_msg_ixs[0], " x0 =", x0, " v =", v, " code_phase[0] =", code_phase[0]
    s = np.cos(carrier_phase) * cacode[code_ixs] * nav_msg[nav_msg_ixs]
    return s

def sat_los(tow, pv, ephem):
    # Iteratively find range to satellite
    tof = 60e-3
    prev_tof = 0
    r_recv = pv[0]
    while np.abs(tof - prev_tof) > 1e-8:
        gps_r, gps_v, clock_err, clock_rate_err = eph.calc_sat_pos(ephem, tow - tof)
        gps_r_sagnac = sagnac(gps_r, tof)
        line_of_sight = gps_r_sagnac - r_recv
        los_range = np.linalg.norm(line_of_sight)
        prev_tof = tof
        tof = los_range / gps.c

    # TODO: sign of clock error and error rate?
    x = los_range - clock_err * gps.c
#    print los_range, x, tof
    # TODO: rotate satellite velocity by sagnac?
    v = np.dot(gps_v - pv[1], line_of_sight) / los_range + clock_rate_err * gps.c
    return x, v

def gen_nav_msg(ephem, tow0, n_subframes=5*25):
    def parity32(x):
        x ^= x >> 16
        x ^= x >> 8
        x ^= x >> 4
        x &= 0xF
        return (0x6996 >> x) & 1

    def add_parity(x, D29star, D30star):
        ''' x: 24-bit data word, as an integer.
            Returns 30-bit word including parity.
        '''
        # Polynomials from libswiftnav:nav_msg.c
        polys = [0xBB1F34A0, 0x5D8F9A50, 0xAEC7CD08,
                 0x5763E684, 0x6BB1F342, 0x8B7A89C1]
        
        x <<= 6 # Make room for the parity bits
        
        if D30star:
            x |= 1<<30

        if D29star:
            x |= 1<<31

        for n in range(0,6):
            x |= parity32(x & polys[n]) << 5-n

        if (D30star):
            x ^= 0x3FFFFFC0  # D30* = 1, invert all the data bits

        return x & 0x3FFFFFFF

    def contrive_parity(x, D29star, D30star):
        # Solve two LSBs for zeros in parity bits D29 and D30
        # x: 24-bit word, two LSBs zero
        # TODO: do this more elegantly
        while add_parity(x, D29star, D30star) & 0x3:
            x += 1
        return x

    def enc(val, scale, bits):
        return np.round(val / (2**scale)).astype(np.int64) & (2**bits - 1)
    
    def gen_sf1(ephem):
        ura_thres=[0, 2.4, 3.4, 4.85, 6.85, 9.65, 13.65, 24, 48, 96, 192,
                   384, 768, 1536, 3072, 6144]
        ura_code = np.searchsorted(ura_thres, int(ephem['sv_accuracy'])) - 1
        word = { 3: ephem['toc'][0] << 14 |
                    int(ephem['l2_codes']) << 12 |
                    ura_code << 8 |
                    int(ephem['health']) << 2 |
                    int(ephem['iodc']) >> 8,
                 4: int(ephem['l2_pflag']) << 23 |
                    0b00101001011010110101101, # Reserved - from rx nav msg archive
                 5: 0b001111100110010101111110,
                 6: 0b001111000100110110010000,
                 7: 0b0100011011111100 << 8 |
                     enc(ephem['tgd'], -31, 8),
                 8: (int(ephem['iodc']) & 0xFF) << 16 |
                     int(ephem['toc'][1] / (2**4)),
                 9: enc(ephem['af2'], -55, 8) << 16 |
                     enc(ephem['af1'], -43, 16),
                 10: enc(ephem['af0'], -31, 22) << 2 }
        return [word[n] for n in range(3,11)]
    
    def gen_sf2(ephem):
        m0 = enc(ephem['m0'] / gps.pi, -31, 32)
        e = enc(ephem['ecc'], -33, 32)
        sqrta = enc(ephem['sqrta'], -19, 32)
        word = { 3: int(ephem['iode']) << 16 |
                    enc(ephem['crs'], -5, 16),
                 4: enc(ephem['dn'] / gps.pi, -43, 16) << 8 |
                    m0 >> 24,
                 5: m0 & 0xFFFFFF,
                 6: enc(ephem['cuc'], -29, 16) << 8 |
                    e >> 24,
                 7: e & 0xFFFFFF,
                 8: enc(ephem['cus'], -29, 16) << 8 |
                    sqrta >> 24,
                 9: sqrta & 0xFFFFFF,
                 10: int(ephem['toe'][1] / (2**4)) << 8 |
                     0b01111100  # fit interval + aodo from rx nav msg archive
        }
        return [word[n] for n in range(3,11)]
    
    def gen_sf3(ephem):
        omega0 = enc(ephem['omega0'] / gps.pi, -31, 32)
        inc = enc(ephem['inc'] / gps.pi, -31, 32)
        w = enc(ephem['w'] / gps.pi, -31, 32)
        word = { 3: enc(ephem['cic'], -29, 16) << 8 |
                    omega0 >> 24,
                 4: omega0 & 0xFFFFFF,
                 5: enc(ephem['cis'], -29, 16) << 8 |
                    inc >> 24,
                 6: inc & 0xFFFFFF,
                 7: enc(ephem['crc'], -5, 16) << 8 |
                    w >> 24,
                 8: w & 0xFFFFFF,
                 9: enc(ephem['omegadot'] / gps.pi, -43, 24),
                 10: int(ephem['iode']) << 16 |
                enc(ephem['inc_dot'] / gps.pi, -43, 14) << 2
        }
        return [word[n] for n in range(3,11)]
    
    def gen_sf4(ephem):
        # Just copy some stuff from the air (PRN01, 2015-05-25 00:00:00)
        navbits = [511488068, 945940202, 386273405, 48194904,
                   631618940, 612991087, 1051748003, 777803572]
        d30star = 0
        words = []
        for rxword in navbits:
            words.append((rxword >> 6) ^ (0xFFFFFF * d30star))
            d30star = rxword & 1
        return words
    
    def gen_sf5(ephem):
        navbits = [293620849, 834459796, 1062420482, 675506260, 
                   742955800, 606233551, 115587165, 981462416]
        d30star = 0
        words = []
        for rxword in navbits:
            words.append((rxword >> 6) ^ (0xFFFFFF * d30star))
            d30star = rxword & 1
        return words

    bits = []
    for subframe_ix in range(n_subframes):
        subframe_id = (subframe_ix % 5) + 1  # 1,2,3,4,5
        tlm = 0b100010110000110011011000 # From received nav msg archive
        
        trunc_tow = tow0 / 6 + subframe_ix + 1 # Start of next sf
        how = (trunc_tow << 7) | (0b01 << 5) | (subframe_id << 2)
        
        words = [tlm, how]
        if subframe_id == 1:
            words += (gen_sf1(ephem))
        if subframe_id == 2:
            words += (gen_sf2(ephem))
        if subframe_id == 3:
            words += (gen_sf3(ephem))
        if subframe_id == 4:
            words += (gen_sf4(ephem))
        if subframe_id == 5:
            words += (gen_sf5(ephem))

        D29star = 0  # Subframe always starts with these zeroed
        D30star = 0  # (contrived via 't' bits in last word of prev sf)
        for ix, word in enumerate(words):
            if ix == 1 or ix == 9:
                word = contrive_parity(word, D29star, D30star)
            wp = add_parity(word, D29star, D30star)
            for b in range(30):
                bits.append(((wp >> (29-b)) & 1) * 2 - 1)
            D29star = (wp & 0x00000002) >> 1
            D30star = (wp & 0x00000001)

    return np.array(bits)

def gen_signal(ephems, traj,
               t0, t_run, t_skip=0, t_step=0.002,
               fs=16.368e6, fi=4.092e6,
               snr=1, jitter=0,
               prns=range(1), scale=16):

    step_t, step_tow, step_pv, step_samps = interp_traj(traj, t0, t_run, t_skip, t_step, fs)
    step_prn_snrs = [{p: snr for p in prns} for t in step_t]
    np.random.seed(222)
    nav_msg_tow0 = int(step_tow[0] / (5*6)) * 5*6 # Round to beginning of 30-second nav msg cycle
    nav_msgs = {prn: gen_nav_msg(ephems[prn], nav_msg_tow0) for prn in prns}
    cacodes = {prn:  np.array(gencode.generateCAcode(prn)) for prn in prns}
#    nav_msgs = {prn: None for prn in prns}
#    cacodes = {prn: np.ones(1023) for prn in prns}
    chunk_len = 10
    def gen_chunk(i, n):
        ss = []
        for ix in range(i, i + n):
            def gen_signal_step_sat(prn):
                x, v = sat_los(step_tow[ix], step_pv[ix], ephems[prn])
                return gen_signal_sat_los(step_tow[ix], x, v, step_samps, fs, fi, cacodes[prn], nav_msgs[prn], nav_msg_tow0, jitter) * step_prn_snrs[ix][prn]
            sp = map(lambda prn: gen_signal_step_sat(prn), step_prn_snrs[ix].keys())
            s = np.sum(sp,0)# + np.random.normal(size=step_samps)
            s = np.int8(s * scale)
            ss.append(s)
        return np.concatenate(ss)
    
    sss = pp.parmap(lambda i: gen_chunk(i, min(chunk_len, len(step_t)-i)),
                    range(0, len(step_t), chunk_len))
    return np.concatenate(sss)

def add_noise(s, level):
    rem = len(s)
    noise = np.random.randn(16*1024*1024)*level
    noise = np.round(noise)
    noise = noise.astype(np.int8)
    i = 0
    while rem:
        n = min(len(noise), rem)
        s[i:i+n] += noise[:n]
        i += n
        rem -= n

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("outfile", help="output filename")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-u", dest="umtfile",
                       help="User Motion Trajectory input file")
    group.add_argument("-e", dest="eceftraj",
                       help="Simple single-point trajectory: "
                       "ECEF x,y,z[,vx,vy,vz[,ax,ay,az[,jx,jy,jz]]] "
                       "e.g. 6378137,0,0")
    parser.add_argument("--no-repair", dest="repair", action='store_false',
                       help="Do not attempt to repair a discontinuous input trajectory")
    parser.add_argument("-s", dest="t_start", default=0.0, type=float,
                       help="Start time (seconds)")
    parser.add_argument("-l", dest="t_run", type=float,
                       help="Run length (seconds)")
    parser.add_argument("-t", dest="t_step", default=0.004, type=float,
                        help="Time step (seconds, default: %(default).3f)")
    parser.add_argument("-g", dest="gps_time", default="2015-05-25 00:00:00",
                       help="GPS time referred to trajectory zero time "
                       '(default: "%(default)s")')
    parser.add_argument("-n", dest="noise", type=int,
                       help="Add Gaussian noise - bear in mind int8 range; "
                       "signal amplitude is 16. NOISE=60 to 70 is reasonable.")
    parser.add_argument("-f", dest="outformat", default="int8",
                        choices=['piksi', 'int8', '1bit', '1bitrev'],
                        help="output file format (default: %(default)s)")
    parser.add_argument("-i", dest="fi", default=4092000.0, type=float,
                       help="Intermediate frequency (default: %(default).0f)")
    parser.add_argument("-r", dest="fs", default=16368000.0, type=float,
                       help="Sampling rate (default: %(default).0f)")
    parser.add_argument("-p", dest="prns",
                       help="Comma-separated 1-indexed PRNs to simulate (default: autoselect)")
    parser.add_argument("-v", dest="verbose", action='store_true',
                       help="Increase verbosity")
    args = parser.parse_args()

    if args.umtfile:
        traj = load_umt(args.umtfile);
        if len(traj) < 1:
            print "Couldn't load any trajectory points from %s." % args.umtfile
            return 1
        traj, smooth = check_smooth(traj, repair=args.repair, verbose=args.verbose)
        if not smooth:
            print "WARNING: Input trajectory may not be sufficiently smooth for cycle-accurate results."
            if args.repair:
                print "Repairs were attempted but may result in momentary large accelerations."

    if args.eceftraj:
        traj = np.array([[0.0] + [float(x) for x in args.eceftraj.split(',')]])
        traj.resize(1,13)  # Pad any unspecified values with zeros
        print "Using Taylor trajectory:"
        print pvaj(traj[0])

    if not args.t_run:
        args.t_run = max(traj[-1][0] - args.t_start, 60)
        print "Run length not specified; using %.1f seconds" % args.t_run
        
    gpst0 = dateutil.parser.parse(args.gps_time)

    settings = peregrine.initSettings.initSettings()        
    ephems = eph.obtain_ephemeris(gpst0, settings)

    if args.prns:
        prns = [int(p) - 1 for p in args.prns.split(',')]
    else:
        [x,y,z] = interp_pv(traj, args.t_start)[0]
        [lat,lon,h] = coord.wgsecef2llh(x,y,z)
        print "Finding satellites visible above %.2f, %.2f, %.0f on %s" % (
            np.degrees(lat), np.degrees(lon), h,
            gpst0 + timedelta(seconds=args.t_start))
        prns = peregrine.warm_start.whatsup(ephems, [x,y,z], gpst0, mask=10)
    print "Using PRNs:", [p + 1 for p in prns]

    print "Generating samples..."
    s = gen_signal(ephems, traj, gpst0,
                   t_run=args.t_run, t_skip=args.t_start, t_step=args.t_step,
                   fs=args.fs, fi=args.fi, prns=prns)
    
    if args.noise:
        print "Adding noise..."
        add_noise(s, args.noise)

    print "Writing output..."
    peregrine.samples.save_samples(args.outfile, s, file_format=args.outformat)
    print "Saved", args.outfile

if __name__ == "__main__":
    sys.exit(main())
