from typing import List, Dict, Tuple, Optional
import logging
import numpy as np
import pandas as pd

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class KeywordClusterer:
    def __init__(self,
                 ngram_range=(1,2),
                 max_k=8,
                 random_state=42):
        self.ngram_range = ngram_range
        self.max_k = max_k
        self.random_state = random_state
        self.vectorizer: Optional[TfidfVectorizer] = None
        self.kmeans: Optional[KMeans] = None
        self.tfidf_matrix = None
        self.feature_names = None

    def fit_transform(self, texts: List[str]):
        self.vectorizer = TfidfVectorizer(ngram_range=self.ngram_range, max_df=0.85, min_df=1)
        self.tfidf_matrix = self.vectorizer.fit_transform(texts)
        self.feature_names = self.vectorizer.get_feature_names_out()
        return self.tfidf_matrix

    def suggest_k(self) -> int:
        """
        Suggest k using a simple elbow heuristic:
        compute inertia for k = 1..max_k and choose k at the largest relative drop elbow.
        """
        if self.tfidf_matrix is None:
            raise ValueError("Call fit_transform first")

        inertias = []
        Ks = list(range(1, min(self.max_k, self.tfidf_matrix.shape[0]) + 1))
        for k in Ks:
            km = KMeans(n_clusters=k, random_state=self.random_state, n_init=10)
            km.fit(self.tfidf_matrix)
            inertias.append(km.inertia_)
        # compute relative drops
        drops = []
        for i in range(1, len(inertias)):
            drop = (inertias[i-1] - inertias[i]) / (inertias[i-1] + 1e-9)
            drops.append(drop)
        if not drops:
            return 1
        # pick k where drop is maximum (elbow)
        max_idx = int(np.argmax(drops))
        suggested_k = Ks[max_idx + 1]  # +1 because drops index corresponds to transition to Ks[i]
        logger.info("Inertias: %s", inertias)
        logger.info("Drops: %s", drops)
        logger.info("Suggested k: %d", suggested_k)
        return suggested_k

    def cluster(self, texts: List[str], k: Optional[int] = None) -> Dict:
        """
        Run clustering and return dict with assignments and top terms.
        """
        if self.tfidf_matrix is None or self.feature_names is None:
            self.fit_transform(texts)

        if k is None:
            k = self.suggest_k()
        if k < 1:
            k = 1

        self.kmeans = KMeans(n_clusters=k, random_state=self.random_state, n_init=20)
        self.kmeans.fit(self.tfidf_matrix)
        labels = self.kmeans.labels_
        centroids = self.kmeans.cluster_centers_

        top_terms_per_cluster = self._top_terms(centroids, top_n=8)
        labels_map = {i: " / ".join(top_terms_per_cluster.get(i, [])[:3]) for i in range(k)}

        return {
            "k": k,
            "labels": labels,
            "top_terms_per_cluster": top_terms_per_cluster,
            "cluster_labels": labels_map,
        }

    def _top_terms(self, centroids, top_n=8):
        top_terms = {}
        for i, centroid in enumerate(centroids):
            # centroid may be dense
            if hasattr(centroid, "toarray"):
                centroid = centroid.toarray().ravel()
            term_idx = np.argsort(centroid)[::-1][:top_n]
            terms = [self.feature_names[idx] for idx in term_idx if centroid[idx] > 0]
            top_terms[i] = terms
        return top_terms

    def transform_texts(self, texts: List[str]):
        if not self.vectorizer:
            raise ValueError("Vectorizer not fitted")
        return self.vectorizer.transform(texts)
