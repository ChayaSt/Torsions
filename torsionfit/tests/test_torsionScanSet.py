""" Test TorsionScanSet """

from torsionfit.tests.utils import get_fun
import torsionfit.TorsionScanSet as torsionset
from cclib.parser import Gaussian
from cclib.parser.utils import convertor
from numpy.testing import assert_almost_equal, assert_array_equal

try:
    from simtk.openmm import app
    HAVE_OPENMM = True
except ImportError:
    HAVE_OPENMM = False


def test_read_logfile():
    structure = get_fun('PRL.psf')
    logfiles = [get_fun('PRL.scan2.neg.log'), get_fun('PRL.scan2.pos.log')]
    scan = torsionset.read_scan_logfile(logfiles, structure)
    scan1 = Gaussian(logfiles[0])
    scan2 = Gaussian(logfiles[1])
    data1 = scan1.parse()
    data2 = scan2.parse()
    assert_almost_equal(scan.xyz[:40]*10, data1.atomcoords, decimal=6)
    assert_almost_equal(scan.xyz[40:]*10, data2.atomcoords, decimal=6)


def test_converged_structures():
    structure = get_fun('MPR.psf')
    scan = torsionset.read_scan_logfile(get_fun('MPR.scan1.pos.log'), structure)
    log = Gaussian(get_fun('MPR.scan1.pos.log'))
    data = log.parse()
    assert_array_equal(data.atomcoords.shape, scan.xyz.shape)
    converted = convertor(data.scfenergies, "eV", "kJmol-1") - \
                min(convertor(data.scfenergies[:len(data.atomcoords)], "eV", "kJmol-1"))
    assert_array_equal(converted[:47], scan.qm_energy)


def test_extract_geom_opt():
    structure = get_fun('MPR.psf')
    scan = torsionset.read_scan_logfile([get_fun('MPR.scan1.pos.log'), get_fun('MPR.scan1.neg.log')], structure)
    scan.extract_geom_opt()

