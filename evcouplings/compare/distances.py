"""
Distance calculations on PDB 3D coordinates

Authors:
  Thomas A. Hopf
"""

import numpy as np
import pandas as pd
from numba import jit


@jit(nopython=True)
def _distances(residues_i, coords_i, residues_j, coords_j, symmetric):
    """
    Compute minimum atom distances between residues. If used on
    a single atom per residue, this function can e.g. also compute
    C_alpha distances.

    Parameters
    ----------
    residues_i : np.array
        Matrix of size N_i x 2, where N_i = number of residues
        in PDB chain used for first axis. Each row of this
        matrix contains the first and last (inclusive) index
        of the atoms comprising this residue in the coords_i
        matrix
    coords_i : np.array
        N_a x 3 matrix containing 3D coordinates of all atoms
        (where N_a is total number of atoms in chain)
    residues_j : np.array
        Like residues_i, but for chain used on second axis
    coords_j : np.array
        Like coords_j, but for chain used on second axis

    Returns
    -------
    dists : np.array
        Matrix of size N_i x N_j containing minimum atom
        distance between residue i and j in dists[i, j]
    """
    LARGE_DIST = 1000000

    N_i, _ = residues_i.shape
    N_j, _ = residues_j.shape

    # matrix to hold final distances
    dists = np.zeros((N_i, N_j))

    # iterate all pairs of residues
    for i in range(N_i):
        for j in range(N_j):
            # limit computation in symmetric case and
            # use previously calculated distance
            if symmetric and i >= j:
                dists[i, j] = dists[j, i]
            else:
                range_i = residues_i[i]
                range_j = residues_j[j]
                min_dist = LARGE_DIST

                # iterate all pairs of atoms for residue pair;
                # end of coord range is inclusive, so have to add 1
                for a_i in range(range_i[0], range_i[1] + 1):
                    for a_j in range(range_j[0], range_j[1] + 1):
                        # compute Euclidean distance between atom pair
                        cur_dist = np.sqrt(
                            np.sum(
                                (coords_i[a_i] - coords_j[a_j]) ** 2
                            )
                        )
                        # store if this is a smaller distance
                        min_dist = min(min_dist, cur_dist)

                dists[i, j] = min_dist

    return dists


class DistanceMap:
    """
    Compute, store and accesss pairwise residue
    distances in PDB 3D structures
    """
    def __init__(self, residues_i, residues_j, dist_matrix, symmetric):
        """
        Create new distance map object

        Parameters
        ----------
        residues_i : pandas.DataFrame
            Table containing residue annotation for
            first axis of distance matrix
        residues_j : pandas.DataFrame
            Table containing residue annotation for
            second axis of distance matrix
        dist_matrix : np.array
            2D matrix containing residue distances
            (of size len(residues_i) x len(residues_j))
        symmetric : bool
            Indicates if distance matrix is symmetric
        """
        self.residues_i = residues_i
        self.residues_j = residues_j
        self.dist_matrix = dist_matrix
        self.symmetric = symmetric

        # create mappings from identifier to entry in distance matrix
        self.id_map_i = {
            id_: i for (i, id_) in enumerate(self.residues_i.id.values)
        }

        self.id_map_j = {
            id_: j for (j, id_) in enumerate(self.residues_j.id.values)
        }

    @classmethod
    def _extract_coords(cls, coords):
        """
        Prepare coordinates as suitable input
        for _distances() function

        Parameters
        ----------
        coords : pandas.DataFrame
            Atom coordinates for PDB chain
            (.coords property of Chain object)

        Returns
        -------
        atom_ranges : np.array
            Matrix of size N_i x 2, where N_i = number
            of residues in PDB chain. Each row of this matrix
            contains the first and last (inclusive) index of
            the atoms comprising this residue in the xyz_coords
            matrix
        xyz_coords : np.array
            N_a x 3 matrix containing 3D coordinates
            of all atoms (where N_a is total number of
            atoms in chain)
        """
        # put indices into column rather than index,
        # so we can access values after groupby
        C = coords.reset_index()

        # matrix of 3D coordinates
        xyz_coords = np.stack(
            (C.x.values, C.y.values, C.z.values)
        ).T

        # extract what the first and last atom index
        # of each residue is
        C_grp = C.groupby("residue_index")
        atom_ranges = np.stack(
            (C_grp.first().loc[:, "index"].values,
             C_grp.last().loc[:, "index"].values)
        ).T

        return atom_ranges, xyz_coords

    @classmethod
    def from_coords(cls, chain_i, chain_j=None):
        """
        Compute distance matrix from PDB chain
        coordinates.

        Parameters
        ----------
        chain_i : Chain
            PDB chain to be used for first axis of matrix
        chain_j : Chain, optional (default: None)
            PDB chain to be used for second axis of matrix.
            If not given, will be set to chain_i, resulting
            in a symmetric distance matrix

        Returns
        -------
        DistanceMap
            Distance map computed from given
            coordinates
        """
        ranges_i, coords_i = cls._extract_coords(chain_i.coords)

        # if no second chain given, compute a symmetric distance
        # matrix (mainly relevant for intra-chain contacts)
        if chain_j is None:
            symmetric = True
            chain_j = chain_i
            ranges_j, coords_j = ranges_i, coords_i
        else:
            symmetric = False
            ranges_j, coords_j = cls._extract_coords(chain_j.coords)

        # compute distances using jit-compiled function
        dists = _distances(
            ranges_i, coords_i,
            ranges_j, coords_j,
            symmetric
        )

        # create distance matrix object
        return cls(
            chain_i.residues, chain_j.residues,
            dists, symmetric
        )

    @classmethod
    def from_file(cls, filename):
        """
        Load existing distance map from file

        Parameters
        ----------
        filename : str
            Path to distance map file

        Returns
        -------
        DistanceMap
            Loaded distance map
        """
        residues = pd.read_csv(
            filename + ".csv", index_col=0,
            dtype={
                "seqres_id": str,
                "coord_id": str,
                "chain_index": int,
            }
        )

        dist_matrix = np.load(filename + ".npy")

        if "axis" in residues.columns:
            symmetric = False
            residues_i = residues.query("axis == 'i'").drop("axis", axis=1)
            residues_j = residues.query("axis == 'j'").drop("axis", axis=1)
        else:
            symmetric = True
            residues_i = residues
            residues_j = residues

        return cls(
            residues_i, residues_j, dist_matrix, symmetric
        )

    def to_file(self, filename):
        """
        Store distance map in file

        Parameters
        ----------
        filename : str
            Prefix of distance map files
            (will create .csv and .npy file)
        """
        def _add_axis(df, axis):
            res = df.copy()
            res.loc[:, "axis"] = axis
            return res

        if self.symmetric:
            residues = self.residues_i
        else:
            res_i = _add_axis(self.residues_i, "i")
            res_j = _add_axis(self.residues_j, "j")
            residues = res_i.append(res_j)

        # save residue table
        residues.to_csv(filename + ".csv", index=True)

        # save distance matrix
        np.save(filename + ".npy", self.dist_matrix)

    def dist(self, i, j, raise_na=True):
        """
        Return distance of residue pair

        Parameters
        ----------
        i : int or str
            Identifier of position on first axis
        j : int or str
            Identifier of position on second axis
        raise_na : bool, optional (default: True)
            Raise error if i or j is not
            contained in either axis. If False,
            returns np.nan for undefined entries.

        Returns
        -------
        np.float
            Distance of pair (i, j). If raise_na
            is False and identifiers are not valid,
            distance will be np.nan

        Raises
        ------
        KeyError
            If index i or j is not a valid identifier
            for respective chain
        """
        # internally all identifiers are handled
        # as strings, so convert
        i, j = str(i), str(j)

        if i not in self.id_map_i:
            if raise_na:
                raise KeyError(
                    "{} not contained in first axis of "
                    "distance map".format(i)
                )
            else:
                return np.nan

        if j not in self.id_map_j:
            if raise_na:
                raise KeyError(
                    "{} not contained in second axis of "
                    "distance map".format(j)
                )
            else:
                return np.nan

        return self.dist_matrix[
            self.id_map_i[i],
            self.id_map_j[j]
        ]

    def __getitem__(self, identifiers):
        """
        Parameters
        ----------
        index : tuple(str, str) or tuple(int, int)
            Identifiers of residues on first and
            second chain

        Raises
        -------
        KeyError
            If either residue identifier not valid
        """
        i, j = identifiers
        return self.dist(i, j, raise_na=True)

    def contacts(self, max_dist=5.0, min_dist=None):
        """
        Return list of pairs below distance threshold

        Parameters
        ----------
        max_dist : float, optional (default: 5.0)
            Maximum distance for any pair to be
            considered a contact
        min_dist : float, optional (default: None)
            Minimum distance of any pair to be
            returned (may be useful if extracting
            different distance ranges from matrix)

        Returns
        -------
        # TODO
        """
        raise NotImplementedError

    def aggregate():
        """
        Aggregate with other distance map(s)

        # TODO: Maybe as classmethod?
        # TODO: how to make sure union has all distances?

        Parameters
        ----------
        # TODO

        Returns
        -------
        # TODO
        """
        raise NotImplementedError
