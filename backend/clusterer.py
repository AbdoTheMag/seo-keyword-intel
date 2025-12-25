# backend/clusterer.py
from typing import List, Dict, Optional, Any
import logging
import numpy as np
import pandas as pd

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.metrics import silhouette_score

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class KeywordClusterer:
    """
    Robust TF-IDF -> SVD -> KMeans clustering with:
    - automatic k suggestion (elbow + silhouette fallback)
    - top terms per cluster (centroid-based)
    - exemplar documents per cluster (closest-to-centroid)
    - output structure friendly for frontend consumption
    """

    def __init__(self,
                 ngram_range=(1,2),
                 max_k=8,
                 svd_components: int = 64,
                 random_state=42):
        self.ngram_range = ngram_range
        self.max_k = max_k
        self.svd_components = svd_components
        self.random_state = random_state

        self.vectorizer: Optional[TfidfVectorizer] = None
        self.tfidf_matrix = None
        self.svd = None
        self.reduced = None
        self.kmeans: Optional[KMeans] = None
        self.feature_names: Optional[List[str]] = None

    def fit_transform(self, texts: List[str]):
        # TF-IDF
        self.vectorizer = TfidfVectorizer(ngram_range=self.ngram_range,
                                          max_df=0.85,
                                          min_df=1,
                                          norm="l2")
        self.tfidf_matrix = self.vectorizer.fit_transform(texts)
        self.feature_names = self.vectorizer.get_feature_names_out()

        # dimensionality reduction for clustering stability (works for sparse matrices)
        n_components = min(self.svd_components, max(2, min(self.tfidf_matrix.shape)-1))
        if n_components >= 2:
            self.svd = TruncatedSVD(n_components=n_components, random_state=self.random_state)
            self.reduced = self.svd.fit_transform(self.tfidf_matrix)
        else:
            self.svd = None
            self.reduced = self.tfidf_matrix.toarray()

        return self.reduced

    def _compute_inertias(self, X, Ks):
        inertias = []
        for k in Ks:
            km = KMeans(n_clusters=k, random_state=self.random_state, n_init=10)
            km.fit(X)
            inertias.append(km.inertia_)
        return inertias

    def suggest_k(self) -> int:
        if self.reduced is None:
            raise ValueError("Call fit_transform first")

        max_k = min(self.max_k, max(2, self.reduced.shape[0]-1))
        Ks = list(range(2, max_k+1))
        if not Ks:
            return 1

        inertias = self._compute_inertias(self.reduced, Ks)

        # simple elbow heuristic: largest relative drop
        drops = [(inertias[i-1] - inertias[i]) / (inertias[i-1] + 1e-9) for i in range(1, len(inertias))]
        if drops:
            best_rel_idx = int(np.argmax(drops))
            suggested_k = Ks[best_rel_idx+0]  # drops index corresponds to transition to Ks[i]
        else:
            suggested_k = Ks[0]

        # sanity: check silhouette for suggested_k; if poor, try fallback
        try:
            km_test = KMeans(n_clusters=suggested_k, random_state=self.random_state, n_init=10)
            labels = km_test.fit_predict(self.reduced)
            if len(set(labels)) > 1:
                sil = silhouette_score(self.reduced, labels)
                if sil < 0.05 and len(Ks) > 1:
                    # pick k with best silhouette among Ks (more robust)
                    best_sil = -1.0
                    best_k = suggested_k
                    for k in Ks:
                        km = KMeans(n_clusters=k, random_state=self.random_state, n_init=10)
                        lab = km.fit_predict(self.reduced)
                        if len(set(lab)) <= 1:
                            continue
                        s = silhouette_score(self.reduced, lab)
                        if s > best_sil:
                            best_sil = s
                            best_k = k
                    suggested_k = best_k
        except Exception:
            # if silhouette fails (tiny samples) just use elbow result
            pass

        return max(1, int(suggested_k))

    def _top_terms(self, centroids, top_n=8):
        # centroids are in reduced space; map back via components if SVD used
        # approximate top terms by projecting centroid into TF-IDF feature space:
        # when SVD used: inverse_transform centroid -> approximate tfidf space
        if self.svd is not None:
            try:
                approx = self.svd.inverse_transform(centroids)
            except Exception:
                approx = centroids
        else:
            approx = centroids

        top_terms = {}
        for i, vec in enumerate(approx):
            idx = np.argsort(vec)[::-1][:top_n]
            terms = [self.feature_names[j] for j in idx if vec[j] > 0]
            top_terms[i] = terms
        return top_terms

    def cluster(self, texts: List[str], k: Optional[int] = None, exemplars_per_cluster: int = 3) -> Dict[str, Any]:
        """
        Run clustering; return dict:
        {
          k, labels (list), top_terms_per_cluster (dict), cluster_labels (dict),
          exemplars: {cluster: [ {text, title, url, position, distance}, ... ]}, silhouette_score (float)
        }
        """
        if self.reduced is None:
            self.fit_transform(texts)

        if k is None:
            k = self.suggest_k()
        if k < 1:
            k = 1

        self.kmeans = KMeans(n_clusters=k, random_state=self.random_state, n_init=20)
        labels = self.kmeans.fit_predict(self.reduced)
        centroids = self.kmeans.cluster_centers_

        # top terms via centroid approximate
        top_terms = self._top_terms(centroids, top_n=8)
        labels_map = {i: " / ".join(top_terms.get(i, [])[:3]) for i in range(k)}

        # exemplars: choose texts closest to centroid (euclidean in reduced space)
        exemplars = {}
        for i in range(k):
            idxs = np.where(labels == i)[0]
            if len(idxs) == 0:
                exemplars[i] = []
                continue
            points = self.reduced[idxs]
            dists = np.linalg.norm(points - centroids[i], axis=1)
            order = np.argsort(dists)[:exemplars_per_cluster]
            exs = []
            for o in order:
                idx = idxs[o]
                exs.append({"index": int(idx), "distance": float(dists[o])})
            exemplars[i] = exs

        # compute silhouette if possible
        sil = None
        try:
            if len(set(labels)) > 1 and len(labels) > len(set(labels)):
                sil = float(silhouette_score(self.reduced, labels))
        except Exception:
            sil = None

        return {
            "k": int(k),
            "labels": labels.tolist() if hasattr(labels, "tolist") else list(labels),
            "top_terms_per_cluster": top_terms,
            "cluster_labels": labels_map,
            "exemplars": exemplars,
            "silhouette": sil
        }

    def transform_texts(self, texts: List[str]):
        if not self.vectorizer:
            raise ValueError("Vectorizer not fitted")
        return self.vectorizer.transform(texts)
