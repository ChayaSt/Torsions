__author__ = 'Chaya D. Stern'

import pymc
from parmed.topologyobjects import DihedralType
import numpy as np
from simtk.unit import kilojoules_per_mole
import torsionfit.TorsionScanSet as TorsionScan


class TorsionFitModel(object):
    """pymc model

    Attributes:
    ----------
    pymc_parameters: dict() of pymc parameters
    parameters_to_optimize: list of tuples (dihedrals to optimize)
    fags: list of TorsionScanSet for fragments
    platform: OpenMM platform to use for potential energy calculations

    """
    def __init__(self, param, frags, stream=None, platform=None, param_to_opt=None, decouple_n=False):
        """Create a PyMC model for fitting torsions.

        Parameters
        ---------
        param : parmed ParameterSet
            Set of parameters that will not be optimized.
        stream : parmed ParameterSet
            Set of parameters including those that will be optimized.
            Existing parameters will be used as initial parameters.
        frags : list of fragments
            List of small molecule fragments with QM torsion data to fit.
        platform : simtk.openmm.Platform
            OpenMM Platform to use for computing potential energies.

        """
        if type(frags) != list:
            frags = [frags]

        self.pymc_parameters = dict()
        self.frags = frags
        self.platform = platform
        self.decouple_n = decouple_n
        if param_to_opt:
            self.parameters_to_optimize = param_to_opt
        else:
            self.parameters_to_optimize = TorsionScan.to_optimize(param, stream)

        multiplicities = [1, 2, 3, 4, 6]
        multiplicity_bitstrings = dict()

        # offset
        for frag in self.frags:
            name = '%s_offset' % frag.topology._residues[0]
            offset = pymc.Uniform(name, lower=-50, upper=50, value=0)
            self.pymc_parameters[name] = offset

        for p in self.parameters_to_optimize:
            torsion_name = p[0] + '_' + p[1] + '_' + p[2] + '_' + p[3]

            if torsion_name not in multiplicity_bitstrings.keys():
                multiplicity_bitstrings[torsion_name] = 0

            for m in multiplicities:
                name = p[0] + '_' + p[1] + '_' + p[2] + '_' + p[3] + '_' + str(m) + '_K'
                k = pymc.Uniform(name, lower=0, upper=20, value=0)
                for i in range(len(param.dihedral_types[p])):
                    if param.dihedral_types[p][i].per == m:
                        multiplicity_bitstrings[torsion_name] += 2 ** (m - 1)
                        k = pymc.Uniform(name, lower=0, upper=20, value=param.dihedral_types[p][i].phi_k)
                        break

                self.pymc_parameters[name] = k

                name = p[0] + '_' + p[1] + '_' + p[2] + '_' + p[3] + '_' + str(m) + '_Phase'
                for i in range(len(param.dihedral_types[p])):
                    if param.dihedral_types[p][i].per == m:
                        if param.dihedral_types[p][i].phase == 0:
                            phase = pymc.DiscreteUniform(name, lower=0, upper=1, value=0)
                            break

                        if param.dihedral_types[p][i].phase == 180.0:
                            phase = pymc.DiscreteUniform(name, lower=0, upper=1, value=1)
                            break
                    else:
                        phase = pymc.DiscreteUniform(name, lower=0, upper=1, value=0)

                self.pymc_parameters[name] = phase

        for torsion_name in multiplicity_bitstrings.keys():
            name = torsion_name + '_multiplicity_bitstring'
            bitstring = pymc.DiscreteUniform(name, lower=0, upper=63, value=multiplicity_bitstrings[torsion_name])
            self.pymc_parameters[name] = bitstring

        self.pymc_parameters['log_sigma'] = pymc.Uniform('log_sigma', lower=-10, upper=3, value=np.log(0.01))
        self.pymc_parameters['sigma'] = pymc.Lambda('sigma',
                                                    lambda log_sigma=self.pymc_parameters['log_sigma']: np.exp(
                                                        log_sigma))
        self.pymc_parameters['precision'] = pymc.Lambda('precision',
                                                        lambda log_sigma=self.pymc_parameters['log_sigma']: np.exp(
                                                            -2 * log_sigma))

        # add missing multiplicity terms to parameterSet so that the system has the same number of parameters
        self.add_missing(param)

        @pymc.deterministic
        def mm_energy(pymc_parameters=self.pymc_parameters, param=param):
            mm = np.ndarray(0)
            self.update_param(param)
            for mol in self.frags:
                mol.compute_energy(param, offset=self.pymc_parameters['%s_offset' % mol.topology._residues[0]],
                                   platform=self.platform)
                mm = np.append(mm, mol.mm_energy / kilojoules_per_mole)
            return mm

        size = sum([len(i.qm_energy) for i in self.frags])
        qm_energy = np.ndarray(0)
        for i in range(len(frags)):
            qm_energy = np.append(qm_energy, frags[i].qm_energy)
        self.pymc_parameters['mm_energy'] = mm_energy
        self.pymc_parameters['qm_fit'] = pymc.Normal('qm_fit', mu=self.pymc_parameters['mm_energy'],
                                                     tau=self.pymc_parameters['precision'], size=size, observed=True,
                                                     value=qm_energy)

    def add_missing(self, param, sample_n5=False):
        """
        Update param set with missing multiplicities.

        :rtype: object
        :param: chemistry.charmm.CharmmParameterSet

        :return: updated CharmmParameterSet with multiplicities 1-6 for parameters to optimize
        """
        multiplicities = [1, 2, 3, 4, 6]
        if sample_n5:
            multiplicities = [1, 2, 3, 4, 5, 6]
        for p in self.parameters_to_optimize:
            reverse = tuple(reversed(p))
            per = []
            for i in range(len(param.dihedral_types[p])):
                per.append(param.dihedral_types[p][i].per)
                per.append(param.dihedral_types[reverse][i].per)
            for j in multiplicities:
                if j not in per:
                    param.dihedral_types[p].append(DihedralType(0, j, 0))
                    param.dihedral_types[reverse].append(DihedralType(0, j, 0))

    def update_param(self, param):
        """
        Update param set based on current pymc model parameters.

        :mol: torsionfit.TorsionScanSet

        :return: updated torsionfit.TorsionScanSet parameters based on current TorsionFitModel parameters
        """

        for p in self.parameters_to_optimize:
            torsion_name = p[0] + '_' + p[1] + '_' + p[2] + '_' + p[3]
            multiplicity_bitstring = self.pymc_parameters[torsion_name + '_multiplicity_bitstring'].value
            reverse_p = tuple(reversed(p))
            for i in range(len(param.dihedral_types[p])):
                m = int(param.dihedral_types[p][i].per)
                multiplicity_bitmask = 2 ** (m - 1)  # multiplicity bitmask
                if (multiplicity_bitstring & multiplicity_bitmask) or self.decouple_n:
                    if m == 5:
                        continue
                    k = torsion_name + '_' + str(m) + '_K'
                    phase = torsion_name + '_' + str(m) + '_Phase'
                    pymc_variable = self.pymc_parameters[k]
                    param.dihedral_types[p][i].phi_k = pymc_variable.value
                    param.dihedral_types[reverse_p][i].phi_k = pymc_variable.value
                    pymc_variable = self.pymc_parameters[phase].value
                    if pymc_variable == 1:
                        param.dihedral_types[p][i].phase = 180
                        param.dihedral_types[reverse_p][i].phase = 180
                        break

                    if pymc_variable == 0:
                        param.dihedral_types[p][i].phase = 0
                        param.dihedral_types[reverse_p][i].phase = 0
                        break
                else:
                    # This torsion periodicity is disabled.
                    param.dihedral_types[p][i].phi_k = 0
                    param.dihedral_types[reverse_p][i].phi_k = 0


class TorsionFitModelContinuousPhase(TorsionFitModel):
    """pymc model

    Attributes:
    ----------
    pymc_parameters: dict() of pymc parameters
    parameters_to_optimize: list of tuples (dihedrals to optimize)
    fags: list of TorsionScanSet for fragments
    platform: OpenMM platform to use for potential energy calculations

    """
    def __init__(self, param, frags, stream=None, platform=None, param_to_opt=None, decouple_n=False):

        """Create a PyMC model for fitting torsions.

        Parameters
        ---------
        param : parmed ParameterSet
            Set of parameters that will not be optimized.
        stream : parmed ParameterSet
            Set of parameters including those that will be optimized.
            Existing parameters will be used as initial parameters.
        frags : list of fragments
            List of small molecule fragments with QM torsion data to fit.
        platform : simtk.openmm.Platform
            OpenMM Platform to use for computing potential energies.

        """

        if type(frags) != list:
            frags = [frags]

        self.pymc_parameters = dict()
        self.frags = frags
        self.platform = platform
        self.decouple_n = decouple_n
        if param_to_opt:
            self.parameters_to_optimize = param_to_opt
        else:
            self.parameters_to_optimize = TorsionScan.to_optimize(param, stream)

        multiplicities = [1, 2, 3, 4, 6]
        multiplicity_bitstrings = dict()

        # offset
        for frag in self.frags:
            name = '%s_offset' % frag.topology._residues[0]
            offset = pymc.Uniform(name, lower=-50, upper=50, value=0)
            self.pymc_parameters[name] = offset

        for p in self.parameters_to_optimize:
            torsion_name = p[0] + '_' + p[1] + '_' + p[2] + '_' + p[3]

            if torsion_name not in multiplicity_bitstrings.keys():
                multiplicity_bitstrings[torsion_name] = 0

            for m in multiplicities:
                name = p[0] + '_' + p[1] + '_' + p[2] + '_' + p[3] + '_' + str(m) + '_K'
                k = pymc.Uniform(name, lower=0, upper=20, value=0)
                for i in range(len(param.dihedral_types[p])):
                    if param.dihedral_types[p][i].per == m:
                        multiplicity_bitstrings[torsion_name] += 2 ** (m - 1)
                        k = pymc.Uniform(name, lower=0, upper=20, value=param.dihedral_types[p][i].phi_k)
                        break

                self.pymc_parameters[name] = k

                name = p[0] + '_' + p[1] + '_' + p[2] + '_' + p[3] + '_' + str(m) + '_Phase'
                for i in range(len(param.dihedral_types[p])):
                    if param.dihedral_types[p][i].per == m:
                        phase = pymc.Uniform(name, lower=0, upper=180.0, value=param.dihedral_types[p][i].phase)
                    else:
                        phase = pymc.Uniform(name, lower=0, upper=180.0, value=0)

                self.pymc_parameters[name] = phase

        for torsion_name in multiplicity_bitstrings.keys():
            name = torsion_name + '_multiplicity_bitstring'
            bitstring = pymc.DiscreteUniform(name, lower=0, upper=63, value=multiplicity_bitstrings[torsion_name])
            self.pymc_parameters[name] = bitstring

        self.pymc_parameters['log_sigma'] = pymc.Uniform('log_sigma', lower=-10, upper=3, value=np.log(0.01))
        self.pymc_parameters['sigma'] = pymc.Lambda('sigma',
                                                    lambda log_sigma=self.pymc_parameters['log_sigma']: np.exp(
                                                        log_sigma))
        self.pymc_parameters['precision'] = pymc.Lambda('precision',
                                                        lambda log_sigma=self.pymc_parameters['log_sigma']: np.exp(
                                                            -2 * log_sigma))

        # add missing multiplicity terms to parameterSet so that the system has the same number of parameters
        self.add_missing(param)


        @pymc.deterministic
        def mm_energy(pymc_parameters=self.pymc_parameters, param=param):
            mm = np.ndarray(0)
            self.update_param(param)
            for mol in self.frags:
                mol.compute_energy(param, offset=self.pymc_parameters['%s_offset' % mol.topology._residues[0]],
                                   platform=self.platform)
                mm = np.append(mm, mol.mm_energy / kilojoules_per_mole)
            return mm

        size = sum([len(i.qm_energy) for i in self.frags])
        qm_energy = np.ndarray(0)
        for i in range(len(frags)):
            qm_energy = np.append(qm_energy, frags[i].qm_energy)
        self.pymc_parameters['mm_energy'] = mm_energy
        self.pymc_parameters['qm_fit'] = pymc.Normal('qm_fit', mu=self.pymc_parameters['mm_energy'],
                                                     tau=self.pymc_parameters['precision'], size=size, observed=True,
                                                     value=qm_energy)

    def update_param(self, param):
        """
        Update param set based on current pymc model parameters.

        :mol: torsionfit.TorsionScanSet

        :return: updated torsionfit.TorsionScanSet parameters based on current TorsionFitModel parameters
        """

        for p in self.parameters_to_optimize:
            torsion_name = p[0] + '_' + p[1] + '_' + p[2] + '_' + p[3]
            multiplicity_bitstring = self.pymc_parameters[torsion_name + '_multiplicity_bitstring'].value
            reverse_p = tuple(reversed(p))
            for i in range(len(param.dihedral_types[p])):
                m = int(param.dihedral_types[p][i].per)
                multiplicity_bitmask = 2 ** (m - 1)  # multiplicity bitmask
                if (multiplicity_bitstring & multiplicity_bitmask) or self.decouple_n:
                    if m == 5:
                        continue
                    k = torsion_name + '_' + str(m) + '_K'
                    pymc_variable = self.pymc_parameters[k]
                    param.dihedral_types[p][i].phi_k = pymc_variable.value
                    param.dihedral_types[reverse_p][i].phi_k = pymc_variable.value
                    phase = torsion_name + '_' + str(m) + '_Phase'
                    pymc_variable = self.pymc_parameters[phase]
                    param.dihedral_types[p][i].phase = pymc_variable.value
                    param.dihedral_types[reverse_p][i].phase = pymc_variable.value
                else:
                    # This torsion periodicity is disabled.
                    param.dihedral_types[p][i].phi_k = 0
                    param.dihedral_types[reverse_p][i].phi_k = 0


class TorsionFitModelEliminatePhase(TorsionFitModel):
    """pymc model

    This model only allows a phase angle of 0 but allows force constants to flip signs. If the sign is negative, the
    phase angle will be 180.

    Attributes:
    ----------
    pymc_parameters: dict() of pymc parameters
    parameters_to_optimize: list of tuples (dihedrals to optimize)
    fags: list of TorsionScanSet for fragments
    platform: OpenMM platform to use for potential energy calculations

    """
    def __init__(self, param, frags, stream=None,  platform=None, param_to_opt=None, decouple_n=False, sample_n5=False):

        """Create a PyMC model for fitting torsions.

        Parameters
        ---------
        param : parmed ParameterSet
            Set of parameters that will not be optimized.
        stream : parmed ParameterSet
            Set of parameters including those that will be optimized.
            Existing parameters will be used as initial parameters.
        frags : list of fragments
            List of small molecule fragments with QM torsion data to fit.
        platform : simtk.openmm.Platform
            OpenMM Platform to use for computing potential energies.

        """

        if type(frags) != list:
            frags = [frags]

        self.pymc_parameters = dict()
        self.frags = frags
        self.platform = platform
        self.decouple_n = decouple_n
        self.sample_n5 = sample_n5
        if param_to_opt:
            self.parameters_to_optimize = param_to_opt
        else:
            self.parameters_to_optimize = TorsionScan.to_optimize(param, stream)

        # set all phases to 0
        self._set_phase_0(param)

        multiplicities = [1, 2, 3, 4, 6]
        if self.sample_n5:
            multiplicities = [1, 2, 3, 4, 5, 6]
        multiplicity_bitstrings = dict()

        # offset
        for frag in self.frags:
            name = '%s_offset' % frag.topology._residues[0]
            offset = pymc.Uniform(name, lower=-50, upper=50, value=0)
            self.pymc_parameters[name] = offset

        for p in self.parameters_to_optimize:
            torsion_name = p[0] + '_' + p[1] + '_' + p[2] + '_' + p[3]

            if torsion_name not in multiplicity_bitstrings.keys():
                multiplicity_bitstrings[torsion_name] = 0

            for m in multiplicities:
                name = p[0] + '_' + p[1] + '_' + p[2] + '_' + p[3] + '_' + str(m) + '_K'
                k = pymc.Uniform(name, lower=-20, upper=20, value=0)
                for i in range(len(param.dihedral_types[p])):
                    if param.dihedral_types[p][i].per == m:
                        multiplicity_bitstrings[torsion_name] += 2 ** (m - 1)
                        k = pymc.Uniform(name, lower=-20, upper=20, value=param.dihedral_types[p][i].phi_k)
                        break

                self.pymc_parameters[name] = k

        for torsion_name in multiplicity_bitstrings.keys():
            name = torsion_name + '_multiplicity_bitstring'
            bitstring = pymc.DiscreteUniform(name, lower=0, upper=63, value=multiplicity_bitstrings[torsion_name])
            self.pymc_parameters[name] = bitstring

        self.pymc_parameters['log_sigma'] = pymc.Uniform('log_sigma', lower=-10, upper=3, value=np.log(0.01))
        self.pymc_parameters['sigma'] = pymc.Lambda('sigma',
                                                    lambda log_sigma=self.pymc_parameters['log_sigma']: np.exp(
                                                        log_sigma))
        self.pymc_parameters['precision'] = pymc.Lambda('precision',
                                                        lambda log_sigma=self.pymc_parameters['log_sigma']: np.exp(
                                                            -2 * log_sigma))

        # add missing multiplicity terms to parameterSet so that the system has the same number of parameters
        self.add_missing(param, sample_n5=self.sample_n5)


        @pymc.deterministic
        def mm_energy(pymc_parameters=self.pymc_parameters, param=param):
            mm = np.ndarray(0)
            self.update_param(param)
            for mol in self.frags:
                mol.compute_energy(param, offset=self.pymc_parameters['%s_offset' % mol.topology._residues[0]],
                                   platform=self.platform)
                mm = np.append(mm, mol.mm_energy / kilojoules_per_mole)
            return mm

        size = sum([len(i.qm_energy) for i in self.frags])
        qm_energy = np.ndarray(0)
        for i in range(len(frags)):
             qm_energy = np.append(qm_energy, frags[i].qm_energy)
        #diff_energy = np.ndarray(0)
        #for i in range(len(frags)):
        #    diff_energy = np.append(diff_energy, frags[i].delta_energy)
        self.pymc_parameters['mm_energy'] = mm_energy
        self.pymc_parameters['qm_fit'] = pymc.Normal('qm_fit', mu=self.pymc_parameters['mm_energy'],
                                                     tau=self.pymc_parameters['precision'], size=size, observed=True,
                                                     value=qm_energy)

    def update_param(self, param):
        """
        Update param set based on current pymc model parameters.

        :mol: torsionfit.TorsionScanSet

        :return: updated torsionfit.TorsionScanSet parameters based on current TorsionFitModel parameters
        """

        for p in self.parameters_to_optimize:
            torsion_name = p[0] + '_' + p[1] + '_' + p[2] + '_' + p[3]
            multiplicity_bitstring = self.pymc_parameters[torsion_name + '_multiplicity_bitstring'].value
            reverse_p = tuple(reversed(p))
            for i in range(len(param.dihedral_types[p])):
                m = int(param.dihedral_types[p][i].per)
                multiplicity_bitmask = 2 ** (m - 1)  # multiplicity bitmask
                if (multiplicity_bitstring & multiplicity_bitmask) or self.decouple_n:
                    if m == 5 and not self.sample_n5:
                        continue
                    k = torsion_name + '_' + str(m) + '_K'
                    pymc_variable = self.pymc_parameters[k]
                    param.dihedral_types[p][i].phi_k = pymc_variable.value
                    param.dihedral_types[reverse_p][i].phi_k = pymc_variable.value

                else:
                    # This torsion periodicity is disabled.
                    param.dihedral_types[p][i].phi_k = 0
                    param.dihedral_types[reverse_p][i].phi_k = 0

    def _set_phase_0(self, param):
        """
        set all phase angles to 0
        :param param: parmed ParameterSet
        """

        for p in self.parameters_to_optimize:
            reverse_p = tuple(reversed(p))
            for i in range(len(param.dihedral_types[p])):
                m = int(param.dihedral_types[p][i].per)
                if m == 5:
                    continue
                param.dihedral_types[p][i].phase = 0
                param.dihedral_types[reverse_p][i].phase = 0