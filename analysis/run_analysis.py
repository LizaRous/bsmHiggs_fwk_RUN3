import os
import sys
import time
import json
import argparse
import uproot
import hist
import warnings
import awkward as ak

from coffea.nanoevents import NanoEventsFactory, NanoAODSchema
from ZH_0lep_total_processor import TOTAL_Processor

warnings.filterwarnings("ignore", message="Missing cross-reference index")

# --- Argument parser ---
parser = argparse.ArgumentParser()
parser.add_argument("--json", type=str, required=True, help="Path to JSON file")
parser.add_argument("--job-index", type=int, required=True)
parser.add_argument("--output", type=str, required=True, help="Histogram output ROOT file")
parser.add_argument("--dataset", type=str, required=True, help="Dataset key inside JSON")
parser.add_argument("--bdt_output", type=str, default=None, help="Optional: output file for BDT trees")
args = parser.parse_args()

# --- Load dataset info ---
with open(args.json) as f:
    all_datasets = json.load(f)

if args.dataset not in all_datasets:
    raise ValueError(f"[ERROR] Dataset '{args.dataset}' not found in {args.json}")

dataset = all_datasets[args.dataset]
meta = dataset["metadata"]
files = dataset["files"]

if args.job_index >= len(files):
    raise IndexError(f"[ERROR] job-index {args.job_index} is out of range (0 - {len(files)-1})")

file_to_process = files[args.job_index]
dataset_name = meta["sample"]
xsec = float(meta["xsec"])
nevts = int(meta["nevents"])
isMC = bool(meta["isMC"])
#### define here if is_MVA
is_MVA = False
print(f"[INFO] Processing file {args.job_index+1}/{len(files)}: {file_to_process}")
print(f"[INFO] Sample: {dataset_name} (xsec={xsec}, nevts={nevts})")

# --- Load NanoAOD events ---
for attempt in range(1, 6):
    try:
        factory = NanoEventsFactory.from_root(
            file_to_process,
            schemaclass=NanoAODSchema,
            uproot_options={"timeout": 300}
        )
        events = factory.events()
        break
    except Exception as e:
        print(f"[WARNING] Attempt {attempt} failed: {e}")
        if attempt == 5:
            print("[ERROR] Max attempts reached. Skipping file.")
            sys.exit(1)
        time.sleep(10)

#  Split TTbar samples to tt+bb tt+cc tt+qq
#  way to split found at: https://github.com/cms-sw/cmssw/blob/master/TopQuarkAnalysis/TopTools/plugins/GenTtbarCategorizer.cc
is_ttbar = dataset_name.startswith("TT")

if is_ttbar  and not is_MVA:
    print("[INFO] TTbar sample detected splitting into ttLF, ttCC, ttBB")
    tt_id = events.genTtbarId

    masks = {
        "ttLF": (tt_id % 100 == 0),
        "ttCC": (tt_id % 100 >= 41) & (tt_id % 100 <= 45),
        "ttBB": (tt_id % 100 >= 51) & (tt_id % 100 <= 55),
    }

    job_suffix = os.path.basename(args.output).split("_")[-1]

    for flavor, mask in masks.items():
        events_flavor = events[mask]
        n_flavor = len(events_flavor)
        print(f"[INFO] Processing flavor: {flavor} (nEvents: {n_flavor})")

        if n_flavor == 0:
            print(f"[INFO] No events found for {flavor} — skipping.")
            continue

        processor_instance = TOTAL_Processor(
            xsec=xsec,
            nevts=nevts,
            isMC=isMC,
            dataset_name=dataset_name,
            is_MVA=False  
        )
        output = processor_instance.process(events_flavor)

        
        sample_base = os.path.basename(dataset_name).replace(".root", "").replace("/", "_")
        out_name = f"{sample_base}_{flavor}_{job_suffix}"

        with uproot.recreate(out_name) as rootfile:
            for name, obj in output.items():
                if isinstance(obj, hist.Hist):
                    rootfile[name] = obj.to_numpy()
        print(f"[INFO] Saved histogram ROOT file: {out_name}")

else:
    # non-TTbar processing 
    processor_instance = TOTAL_Processor(
        xsec=xsec,
        nevts=nevts,
        isMC=isMC,
        dataset_name=dataset_name,
        is_MVA=True
    )
    output = processor_instance.process(events)

    output_name = args.output
    with uproot.recreate(output_name) as rootfile:
        for name, obj in output.items():
            if isinstance(obj, hist.Hist):
                rootfile[name] = obj.to_numpy()
    print(f"[INFO] Saved histogram ROOT file: {output_name}")

    # --- Save BDT trees ---
    bdt_output_name = args.bdt_output or f"bdt_{os.path.basename(args.output)}"
    tree_data = output.get("trees", None)

    if not tree_data and hasattr(processor_instance, "_trees"):
        tree_data = processor_instance._trees

    if tree_data:
        with uproot.recreate(bdt_output_name) as bdtfile:
            for regime, tree_dict in tree_data.items():
                bdtfile[regime] = tree_dict
        print(f"[INFO] Saved BDT training trees in: {bdt_output_name}")
    else:
        print("[WARNING] No BDT trees found — nothing was written to tree output file.")
