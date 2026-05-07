import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

STYLES = {
    "uncompressed": {"color": "black", "marker": "s", "linestyle": "--"},
    "turboquant_v2": {"color": "red", "marker": "o"},
    "turboquant_v1": {"color": "salmon", "marker": "x", "linestyle": ":"},
    "qjl_v2": {"color": "blue", "marker": "o"},
    "qjl_v1": {"color": "lightblue", "marker": "x", "linestyle": ":"},
    "polarquant_v2": {"color": "green", "marker": "o"},
    "polarquant_v1": {"color": "lightgreen", "marker": "x", "linestyle": ":"},
}

def load_and_merge():
    # Load V1
    results_v1 = pd.read_csv("results_v1/data_main_results.csv")
    profiles_v1 = pd.read_csv("results_v1/data_profiles.csv")
    
    # Load V2
    results_v2 = pd.read_csv("results_v2/data_main_results.csv")
    profiles_v2 = pd.read_csv("results_v2/data_profiles.csv")

    # Rename methods
    results_v1["method"] = results_v1["method"] + "_v1"
    profiles_v1["method"] = profiles_v1["method"] + "_v1"
    results_v2["method"] = results_v2["method"] + "_v2"
    profiles_v2["method"] = profiles_v2["method"] + "_v2"

    results_v2.loc[results_v2["method"] == "uncompressed_v2", "method"] = "uncompressed"
    profiles_v2.loc[profiles_v2["method"] == "uncompressed_v2", "method"] = "uncompressed"

    results = pd.concat([results_v1, results_v2])
    profiles = pd.concat([profiles_v1, profiles_v2])
    return results, profiles

def plot_memory_tradeoff(results, profiles):
    recall = results.groupby(["method", "bits"], as_index=False)["recall_at_10"].mean()
    prof = profiles.groupby(["method", "bits"], as_index=False)["bytes_per_vector"].mean()
    merged = recall.merge(prof, on=["method", "bits"])

    fig, ax = plt.subplots(figsize=(8, 6))
    for method, sub in merged.groupby("method"):
        s = sub.sort_values("bytes_per_vector")
        ax.plot(s["bytes_per_vector"], s["recall_at_10"], label=method, **STYLES.get(method, {"marker": "o"}))
    ax.set_xlabel("Bytes per Vector (log)")
    ax.set_ylabel("Mean Recall@10")
    ax.set_xscale("log")
    ax.grid(alpha=0.3)
    ax.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
    fig.suptitle("V1 vs V2: Memory-Accuracy Tradeoff")
    fig.tight_layout()
    fig.savefig("results/v1_vs_v2_memory.png", dpi=150)

def plot_latency_and_time(profiles):
    prof = profiles.groupby(["method", "bits"], as_index=False)[["query_latency_ms", "compress_seconds", "bytes_per_vector"]].mean()
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    for method, sub in prof.groupby("method"):
        s = sub.sort_values("bytes_per_vector")
        style = STYLES.get(method, {"marker": "o"})
        ax1.plot(s["bytes_per_vector"], s["query_latency_ms"], label=method, **style)
        ax2.plot(s["bytes_per_vector"], s["compress_seconds"], label=method, **style)
        
    ax1.set_xlabel("Bytes per Vector (log)")
    ax1.set_ylabel("Query Latency (ms)")
    ax1.set_xscale("log")
    ax1.grid(alpha=0.3)
    ax1.legend()
    ax1.set_title("Search Latency")

    ax2.set_xlabel("Bytes per Vector (log)")
    ax2.set_ylabel("Compression Time (s)")
    ax2.set_xscale("log")
    ax2.grid(alpha=0.3)
    ax2.set_title("Index Build Time")

    fig.suptitle("V1 vs V2: Speed and Latency Tradeoffs")
    fig.tight_layout()
    fig.savefig("results/v1_vs_v2_speed.png", dpi=150)

def plot_recall_metrics(results):
    agg = results.groupby(["method", "bits"], as_index=False)[["recall_at_1", "recall_at_5", "recall_at_10"]].mean()
    
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    metrics = [("recall_at_1", "Recall@1"), ("recall_at_5", "Recall@5"), ("recall_at_10", "Recall@10")]
    
    for ax, (col, title) in zip(axes, metrics):
        for method, sub in agg.groupby("method"):
            s = sub.sort_values("bits")
            ax.plot(s["bits"], s[col], label=method, **STYLES.get(method, {"marker": "o"}))
        ax.set_xlabel("Bits per dim")
        ax.set_ylabel(title)
        ax.grid(alpha=0.3)
        ax.set_title(title)
    
    axes[-1].legend(bbox_to_anchor=(1.05, 1), loc='upper left')
    fig.suptitle("V1 vs V2: All Recall Metrics")
    fig.tight_layout()
    fig.savefig("results/v1_vs_v2_all_recalls.png", dpi=150)

def plot_cross_vs_same(results):
    cross = {"T1_text2image", "T2_image2text"}
    results["modality"] = results["task"].apply(lambda t: "cross-modal" if t in cross else "same-modal")
    
    agg = results.groupby(["method", "bits", "modality"], as_index=False)["recall_at_10"].mean()
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    
    for mod, ax in zip(["cross-modal", "same-modal"], [ax1, ax2]):
        sub_mod = agg[agg["modality"] == mod]
        for method, sub in sub_mod.groupby("method"):
            s = sub.sort_values("bits")
            ax.plot(s["bits"], s["recall_at_10"], label=method, **STYLES.get(method, {"marker": "o"}))
        ax.set_xlabel("Bits per dim")
        ax.set_title(f"{mod.capitalize()} Recall@10")
        ax.grid(alpha=0.3)
        
    ax2.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
    fig.suptitle("V1 vs V2: Cross-Modal vs Same-Modal")
    fig.tight_layout()
    fig.savefig("results/v1_vs_v2_modalities.png", dpi=150)

def main():
    Path("results").mkdir(exist_ok=True)
    try:
        results, profiles = load_and_merge()
        plot_memory_tradeoff(results, profiles)
        plot_latency_and_time(profiles)
        plot_recall_metrics(results)
        plot_cross_vs_same(results)
        print("Successfully generated all plots in results/")
    except Exception as e:
        print(f"Error plotting: {e}. Note: Make sure the sweeps have generated enough data!")

if __name__ == "__main__":
    main()
