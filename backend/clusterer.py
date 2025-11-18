from typing import List, Dict, Optional
import logging
import numpy as np

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

class KeywordClusterer:
    def __init__(self,
                 ngram_range=(1, 2),
                 max_k=8,
                 random_state=42):
        self.ngram_range = ngram_range
        self.max_k = max_k
        self.random_state = random_state

        self.vectorizer: Optional[TfidfVectorizer] = None
        self.tfidf = None
        self.kmeans: Optional[KMeans] = None
        self.features = None

    def fit_transform(self, texts: List[str]):
        vec = TfidfVectorizer(
            ngram_range=self.ngram_range,
            max_df=0.85,
            min_df=1,
            norm="l2",
            sublinear_tf=True
        )
        self.vectorizer = vec
        self.tfidf = vec.fit_transform(texts)
        self.features = vec.get_feature_names_out()
        return self.tfidf

    def _inertias(self, ks):
        vals = []
        for k in ks:
            km = KMeans(
                n_clusters=k,
                random_state=self.random_state,
                n_init=20
            ).fit(self.tfidf)
            vals.append(km.inertia_)
        return vals

    def suggest_k(self) -> int:
        if self.tfidf is None:
            raise ValueError("fit_transform must be called first")

        limit = min(self.max_k, self.tfidf.shape[0])
        ks = list(range(1, limit + 1))
        inertias = self._inertias(ks)

        if len(inertias) < 2:
            return 1

        drops = np.diff(inertias) * -1 / (np.array(inertias[:-1]) + 1e-9)
        idx = int(np.argmax(drops))
        k = ks[idx + 1]

        log.info("Inertias %s", inertias)
        log.info("Drops %s", drops.tolist())
        log.info("Suggested k %d", k)
        return k

    def cluster(self, texts: List[str], k: Optional[int] = None) -> Dict:
        if self.tfidf is None:
            self.fit_transform(texts)

        if k is None:
            k = self.suggest_k()
        if k < 1:
            k = 1

        km = KMeans(
            n_clusters=k,
            random_state=self.random_state,
            n_init=20
        ).fit(self.tfidf)

        self.kmeans = km
        labels = km.labels_
        centers = km.cluster_centers_

        tops = self._top_terms(centers, top_n=8)
        readable = {i: " / ".join(tops[i][:3]) for i in range(k)}

        return {
            "k": k,
            "labels": labels,
            "top_terms_per_cluster": tops,
            "cluster_labels": readable
        }

    def _top_terms(self, centers, top_n=8):
        out = {}
        for i, row in enumerate(centers):
            # ensure dense
            vec = row if not hasattr(row, "toarray") else row.toarray().ravel()
            idx = np.argsort(vec)[::-1][:top_n]
            out[i] = [self.features[j] for j in idx if vec[j] > 0]
        return out

    def transform_texts(self, texts: List[str]):
        if self.vectorizer is None:
            raise ValueError("vectorizer not fitted")
        return self.vectorizer.transform(texts)
