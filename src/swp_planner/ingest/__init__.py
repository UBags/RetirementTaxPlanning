# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""ingest -- pulling outside data in, and recording what it does not say.

    nsdl_securities  the NSDL active securities list: ISIN, issuer, credit
                     rating, sector and ownership for the corporate debt
                     universe.

THE SOURCE IS SURVIVORSHIP-BIASED AND NO CODE HERE FIXES THAT. NSDL's endpoint
serves only ACTIVE instruments -- type=Redeemed, All and Inactive all return
400 -- so DHFL's and IL&FS's paper is absent. What can be recovered is
disappearance between snapshots, which is why the loader records what went
missing rather than only what arrived.
"""
from . import nsdl_securities  # noqa: F401

__all__ = ["nsdl_securities"]
