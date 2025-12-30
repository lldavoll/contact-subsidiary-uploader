"""
Main script that uploads contact info and subsidiaries to Firebase.
This is what you run to process your CSV files.
Everything is in one file for simplicity.
"""
import csv
import json
import sys
import re
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
from pathlib import Path
import firebase_admin
from firebase_admin import credentials, firestore
import rapidfuzz
from rapidfuzz import fuzz


# ============================================================================
# DATA FILTERING
# ============================================================================

def is_extraction_error(row: Dict[str, Any]) -> bool:
    """Check if a row looks like an extraction error."""
    subsidiary_raw = str(row.get('subsidiary_name_raw', '')).strip()
    subsidiary_clean = str(row.get('subsidiary_name_clean', '')).strip()
    
    narrative_patterns = [
        r'the following is a list',
        r'omitting subsidiaries',
        r'considered in the aggregate',
        r'company name',
        r'^name$',
        r'subsidiaries? of',
        r'as of \w+ \d+',
    ]
    
    for pattern in narrative_patterns:
        if re.search(pattern, subsidiary_raw, re.IGNORECASE) or \
           re.search(pattern, subsidiary_clean, re.IGNORECASE):
            return True
    
    if subsidiary_clean.lower() in ['name', 'company', 'subsidiary', 'company name']:
        return True
    
    return False


def is_incomplete_subsidiary_data(row: Dict[str, Any]) -> bool:
    """Check if subsidiary data is incomplete."""
    subsidiary_count = row.get('subsidiary_count', 0)
    subsidiary_raw = str(row.get('subsidiary_name_raw', '')).strip()
    
    try:
        if isinstance(subsidiary_count, str):
            subsidiary_count = int(subsidiary_count) if subsidiary_count else 0
        else:
            subsidiary_count = int(subsidiary_count)
    except (ValueError, TypeError):
        subsidiary_count = 0
    
    if subsidiary_count > 0 and not subsidiary_raw:
        return True
    
    return False


def filter_subsidiary_data(rows: List[Dict[str, Any]]) -> tuple:
    """Filter out bad subsidiary data."""
    filtered_rows = []
    filtered_count = 0
    incomplete_count = 0
    
    for row in rows:
        if is_extraction_error(row):
            filtered_count += 1
            continue
        
        if is_incomplete_subsidiary_data(row):
            incomplete_count += 1
            continue
        
        filtered_rows.append(row)
    
    return filtered_rows, filtered_count, incomplete_count


def filter_contacts_data(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter contacts data - currently just returns all rows."""
    return rows


# ============================================================================
# NAME NORMALIZATION
# ============================================================================

COMPANY_SUFFIXES = [
    r'\binc\.?\b', r'\bincorporated\b', r'\bcorp\.?\b', r'\bcorporation\b',
    r'\bllc\.?\b', r'\bl\.l\.c\.?\b', r'\blimited\b', r'\bltd\.?\b',
    r'\bco\.?\b', r'\bcompany\b', r'\bplc\.?\b', r'\bp\.l\.c\.?\b',
    r'\bs\.a\.?\b', r'\bsa\b', r'\bag\b', r'\bgmbh\b', r'\bllp\.?\b',
    r'\bl\.p\.?\b', r'\bholdings?\b', r'\bgroup\b', r'\benterprises?\b',
    r'\bindustries?\b', r'\bsystems?\b', r'\btechnologies?\b', r'\btech\b',
    r'\bintl\.?\b', r'\binternational\b', r'\bglobal\b',
]


def normalize_company_name(name: Optional[str]) -> str:
    """Clean up company names so they're easier to match."""
    if not name or not isinstance(name, str):
        return ""
    
    normalized = name.lower().strip()
    normalized = normalized.replace('&', ' and ')
    normalized = normalized.replace('@', ' at ')
    normalized = normalized.replace('+', ' plus ')
    normalized = normalized.replace('/', ' ')
    normalized = normalized.replace('-', ' ')
    normalized = normalized.replace('_', ' ')
    normalized = re.sub(r'[^\w\s]', ' ', normalized)
    
    for suffix_pattern in COMPANY_SUFFIXES:
        normalized = re.sub(suffix_pattern, ' ', normalized, flags=re.IGNORECASE)
    
    normalized = re.sub(r'\b\d+\b', '', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    
    return normalized


# ============================================================================
# FUZZY MATCHING
# ============================================================================

class FuzzyMatcher:
    """Matches company names even when they're not exactly the same."""
    
    def __init__(
        self,
        auto_accept_threshold: float = 90.0,
        manual_review_threshold: float = 80.0,
        reject_threshold: float = 80.0
    ):
        self.auto_accept_threshold = auto_accept_threshold
        self.manual_review_threshold = manual_review_threshold
        self.reject_threshold = reject_threshold
    
    def calculate_similarity(self, str1: str, str2: str) -> float:
        """Calculate how similar two strings are."""
        if not str1 or not str2:
            return 0.0
        
        ratio_score = fuzz.ratio(str1, str2)
        partial_ratio = fuzz.partial_ratio(str1, str2)
        token_sort_ratio = fuzz.token_sort_ratio(str1, str2)
        token_set_ratio = fuzz.token_set_ratio(str1, str2)
        
        best_score = max(ratio_score, partial_ratio, token_sort_ratio, token_set_ratio)
        return float(best_score)
    
    def find_best_match(
        self,
        query: str,
        candidates: List[Tuple[str, Dict]],
        normalized_candidates: Optional[List[str]] = None
    ) -> Tuple[Optional[Dict], float, str]:
        """Find the best match for a company name."""
        if not query or not candidates:
            return None, 0.0, 'reject'
        
        best_match = None
        best_score = 0.0
        
        if normalized_candidates is None:
            normalized_candidates = [cand[0] for cand in candidates]
        
        for i, (normalized_cand, brand_data) in enumerate(candidates):
            score = self.calculate_similarity(query, normalized_candidates[i])
            
            if score > best_score:
                best_score = score
                best_match = brand_data
        
        if best_score >= self.auto_accept_threshold:
            status = 'auto_accept'
        elif best_score >= self.manual_review_threshold:
            status = 'manual_review'
        else:
            status = 'reject'
        
        return best_match, best_score, status
    
    def find_all_matches(
        self,
        query: str,
        candidates: List[Tuple[str, Dict]],
        normalized_candidates: Optional[List[str]] = None,
        limit: int = 5
    ) -> List[Tuple[Dict, float]]:
        """Find top N matches for manual review."""
        if not query or not candidates:
            return []
        
        matches = []
        
        if normalized_candidates is None:
            normalized_candidates = [cand[0] for cand in candidates]
        
        for i, (normalized_cand, brand_data) in enumerate(candidates):
            score = self.calculate_similarity(query, normalized_candidates[i])
            matches.append((brand_data, score))
        
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[:limit]


# ============================================================================
# FIREBASE CLIENT
# ============================================================================

class FirebaseClient:
    """Handles talking to Firebase - reading and writing brand information."""
    
    def __init__(self, credentials_path: Optional[str] = None, project_id: Optional[str] = None):
        self.db = None
        self.initialized = False
        
        if credentials_path:
            self.initialize(credentials_path, project_id)
    
    def initialize(self, credentials_path: str, project_id: Optional[str] = None):
        """Set up connection to Firebase."""
        try:
            cred = credentials.Certificate(credentials_path)
            if project_id:
                firebase_admin.initialize_app(cred, {'projectId': project_id})
            else:
                firebase_admin.initialize_app(cred)
            
            self.db = firestore.client()
            self.initialized = True
        except Exception as e:
            raise Exception(f"Failed to initialize Firebase: {str(e)}")
    
    def get_all_brands(self) -> Dict[str, Dict]:
        """Get all brands from Firebase."""
        if not self.initialized:
            raise Exception("Firebase not initialized. Call initialize() first.")
        
        brands = {}
        brands_ref = self.db.collection('brands')
        
        for doc in brands_ref.stream():
            brand_data = doc.to_dict()
            brand_data['brand_id'] = doc.id
            brands[doc.id] = brand_data
        
        return brands
    
    def get_brand_name_field(self) -> str:
        """Figure out which field has the brand name."""
        if not self.initialized:
            raise Exception("Firebase not initialized. Call initialize() first.")
        
        brands_ref = self.db.collection('brands').limit(10)
        name_fields = ['name', 'company_name', 'brand_name', 'title']
        
        for doc in brands_ref.stream():
            brand_data = doc.to_dict()
            for field in name_fields:
                if field in brand_data and isinstance(brand_data[field], str):
                    return field
        
        return 'name'
    
    def get_existing_social_keys(self) -> List[str]:
        """Get list of social keys already used in Firebase."""
        if not self.initialized:
            raise Exception("Firebase not initialized. Call initialize() first.")
        
        social_keys = set()
        brands_ref = self.db.collection('brands')
        
        for doc in brands_ref.stream():
            brand_data = doc.to_dict()
            social_data = brand_data.get('social', {})
            
            if isinstance(social_data, dict):
                social_keys.update(social_data.keys())
        
        return sorted(list(social_keys))
    
    def update_brand_social(
        self,
        brand_id: str,
        social_updates: Dict[str, Any],
        dry_run: bool = False
    ) -> bool:
        """Update contact info for a brand."""
        if not self.initialized:
            raise Exception("Firebase not initialized. Call initialize() first.")
        
        if dry_run:
            return True
        
        try:
            doc_ref = self.db.collection('brands').document(brand_id)
            
            doc = doc_ref.get()
            existing_social = {}
            if doc.exists:
                existing_data = doc.to_dict()
                existing_social = existing_data.get('social', {})
            
            updated_social = {**existing_social, **social_updates}
            doc_ref.update({'social': updated_social})
            return True
        
        except Exception as e:
            print(f"Error updating social for brand {brand_id}: {str(e)}")
            return False
    
    def update_brand_parent_info(
        self,
        brand_id: str,
        parent_company: Optional[str] = None,
        parent_id: Optional[str] = None,
        dry_run: bool = False
    ) -> bool:
        """Update parent company info for a subsidiary."""
        if not self.initialized:
            raise Exception("Firebase not initialized. Call initialize() first.")
        
        if dry_run:
            return True
        
        try:
            doc_ref = self.db.collection('brands').document(brand_id)
            updates = {}
            
            if parent_company is not None:
                updates['parent_company'] = parent_company
            if parent_id is not None:
                updates['parent_id'] = parent_id
            
            if updates:
                doc_ref.update(updates)
            
            return True
        
        except Exception as e:
            print(f"Error updating parent info for brand {brand_id}: {str(e)}")
            return False
    
    def update_parent_subsidiaries(
        self,
        parent_id: str,
        subsidiary_ids: List[str],
        dry_run: bool = False
    ) -> bool:
        """Update subsidiaries list for a parent company."""
        if not self.initialized:
            raise Exception("Firebase not initialized. Call initialize() first.")
        
        if dry_run:
            return True
        
        try:
            doc_ref = self.db.collection('brands').document(parent_id)
            
            doc = doc_ref.get()
            existing_subsidiaries = {}
            if doc.exists:
                existing_data = doc.to_dict()
                existing_subsidiaries = existing_data.get('subsidiaries', {})
            
            updated_subsidiaries = {**existing_subsidiaries}
            for sub_id in subsidiary_ids:
                updated_subsidiaries[sub_id] = True
            
            doc_ref.update({'subsidiaries': updated_subsidiaries})
            return True
        
        except Exception as e:
            print(f"Error updating subsidiaries for parent {parent_id}: {str(e)}")
            return False


# ============================================================================
# MAIN UPLOADER
# ============================================================================

class DataUploader:
    """Main class for uploading contact and subsidiary data to Firebase."""
    
    def __init__(
        self,
        firebase_client: FirebaseClient,
        fuzzy_matcher: FuzzyMatcher,
        dry_run: bool = False,
        single_company: Optional[str] = None
    ):
        self.firebase_client = firebase_client
        self.fuzzy_matcher = fuzzy_matcher
        self.dry_run = dry_run
        self.single_company = single_company
        
        self.stats = {
            'contacts_processed': 0,
            'contacts_matched': 0,
            'contacts_auto_accepted': 0,
            'contacts_manual_review': 0,
            'contacts_rejected': 0,
            'contacts_unmatched': 0,
            'subsidiaries_processed': 0,
            'subsidiaries_matched': 0,
            'subsidiaries_auto_accepted': 0,
            'subsidiaries_manual_review': 0,
            'subsidiaries_rejected': 0,
            'subsidiaries_unmatched': 0,
            'errors': 0,
        }
        
        self.manual_review_queue = []
        self.unmatched_companies = []
        self.brands_cache = None
        self.brand_name_field = None
        self.social_keys_mapping = None
    
    def load_brands_cache(self):
        """Load all brands from Firebase."""
        print("Loading brands from Firebase...")
        self.brands_cache = self.firebase_client.get_all_brands()
        self.brand_name_field = self.firebase_client.get_brand_name_field()
        print(f"Loaded {len(self.brands_cache)} brands")
        
        existing_keys = self.firebase_client.get_existing_social_keys()
        print(f"Found existing social keys: {existing_keys}")
        
        self.social_keys_mapping = {
            'twitter_url': 'twitter',
            'facebook_url': 'facebook',
            'bluesky_url': 'bluesky',
            'ir_email': 'ir_email',
            'cs_email': 'cs_email',
            'ir_page': 'ir_page',
            'cs_page': 'cs_page',
            'domain': 'website',
        }
    
    def prepare_brands_for_matching(self) -> List[Tuple[str, Dict]]:
        """Prepare brands for fuzzy matching."""
        if not self.brands_cache:
            self.load_brands_cache()
        
        brands_for_matching = []
        for brand_id, brand_data in self.brands_cache.items():
            brand_name = brand_data.get(self.brand_name_field, '')
            normalized_name = normalize_company_name(brand_name)
            brands_for_matching.append((normalized_name, brand_data))
        
        return brands_for_matching
    
    def load_contacts_csv(self, filepath: str) -> List[Dict]:
        """Load contacts CSV file."""
        contacts = []
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                contacts.append(row)
        
        contacts = filter_contacts_data(contacts)
        return contacts
    
    def load_subsidiary_csv(self, filepath: str) -> List[Dict]:
        """Load subsidiary CSV file."""
        subsidiaries = []
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                subsidiaries.append(row)
        
        filtered, error_count, incomplete_count = filter_subsidiary_data(subsidiaries)
        print(f"Filtered {error_count} extraction errors")
        print(f"Excluded {incomplete_count} incomplete subsidiary entries")
        
        return filtered
    
    def process_contacts(
        self,
        contacts: List[Dict],
        brands_for_matching: List[Tuple[str, Dict]]
    ):
        """Process and upload contact information."""
        print("\n" + "="*60)
        print("Processing Contacts")
        print("="*60)
        
        for contact in contacts:
            company_name = contact.get('company_clean', '').strip()
            
            if self.single_company and company_name.lower() != self.single_company.lower():
                continue
            
            if not company_name:
                continue
            
            self.stats['contacts_processed'] += 1
            
            normalized_name = normalize_company_name(company_name)
            
            best_match, score, status = self.fuzzy_matcher.find_best_match(
                normalized_name,
                brands_for_matching
            )
            
            if status == 'reject' or not best_match:
                self.stats['contacts_rejected'] += 1
                self.unmatched_companies.append({
                    'type': 'contact',
                    'company_name': company_name,
                    'normalized': normalized_name,
                    'score': score
                })
                print(f"REJECTED: {company_name} (score: {score:.1f}%)")
                continue
            
            brand_id = best_match.get('brand_id')
            
            if status == 'auto_accept':
                self.stats['contacts_auto_accepted'] += 1
                self.stats['contacts_matched'] += 1
                self._upload_contact_info(brand_id, contact, company_name, score)
            elif status == 'manual_review':
                self.stats['contacts_manual_review'] += 1
                self.manual_review_queue.append({
                    'type': 'contact',
                    'company_name': company_name,
                    'normalized': normalized_name,
                    'brand_match': best_match,
                    'score': score,
                    'contact_data': contact,
                    'top_matches': self.fuzzy_matcher.find_all_matches(
                        normalized_name, brands_for_matching, limit=5
                    )
                })
                print(f"MANUAL REVIEW: {company_name} -> {best_match.get(self.brand_name_field)} (score: {score:.1f}%)")
        
        print(f"\nContacts Summary:")
        print(f"  Processed: {self.stats['contacts_processed']}")
        print(f"  Auto-accepted: {self.stats['contacts_auto_accepted']}")
        print(f"  Manual review: {self.stats['contacts_manual_review']}")
        print(f"  Rejected: {self.stats['contacts_rejected']}")
    
    def process_subsidiaries(
        self,
        subsidiaries: List[Dict],
        brands_for_matching: List[Tuple[str, Dict]]
    ):
        """Process and upload subsidiary information."""
        print("\n" + "="*60)
        print("Processing Subsidiaries")
        print("="*60)
        
        parent_subsidiaries = defaultdict(list)
        
        for row in subsidiaries:
            parent_name = row.get('company_name', '').strip()
            subsidiary_raw = row.get('subsidiary_name_raw', '').strip()
            
            if parent_name and subsidiary_raw:
                parent_subsidiaries[parent_name].append(row)
        
        for parent_name, subs_list in parent_subsidiaries.items():
            if self.single_company and parent_name.lower() != self.single_company.lower():
                continue
            
            self.stats['subsidiaries_processed'] += 1
            
            normalized_parent = normalize_company_name(parent_name)
            
            parent_match, parent_score, parent_status = self.fuzzy_matcher.find_best_match(
                normalized_parent,
                brands_for_matching
            )
            
            if parent_status == 'reject' or not parent_match:
                self.stats['subsidiaries_rejected'] += 1
                self.unmatched_companies.append({
                    'type': 'subsidiary_parent',
                    'company_name': parent_name,
                    'normalized': normalized_parent,
                    'score': parent_score
                })
                print(f"REJECTED PARENT: {parent_name} (score: {parent_score:.1f}%)")
                continue
            
            parent_brand_id = parent_match.get('brand_id')
            matched_subsidiaries = []
            
            for sub_row in subs_list:
                subsidiary_name = sub_row.get('subsidiary_name_raw', '').strip()
                normalized_sub = normalize_company_name(subsidiary_name)
                
                sub_match, sub_score, sub_status = self.fuzzy_matcher.find_best_match(
                    normalized_sub,
                    brands_for_matching
                )
                
                if sub_status == 'reject' or not sub_match:
                    self.unmatched_companies.append({
                        'type': 'subsidiary',
                        'company_name': subsidiary_name,
                        'parent': parent_name,
                        'normalized': normalized_sub,
                        'score': sub_score
                    })
                    continue
                
                sub_brand_id = sub_match.get('brand_id')
                
                if sub_status == 'auto_accept':
                    matched_subsidiaries.append((sub_brand_id, sub_match, sub_score))
                elif sub_status == 'manual_review':
                    self.manual_review_queue.append({
                        'type': 'subsidiary',
                        'parent_name': parent_name,
                        'parent_brand': parent_match,
                        'subsidiary_name': subsidiary_name,
                        'normalized': normalized_sub,
                        'subsidiary_brand': sub_match,
                        'score': sub_score,
                        'top_matches': self.fuzzy_matcher.find_all_matches(
                            normalized_sub, brands_for_matching, limit=5
                        )
                    })
            
            if parent_status == 'auto_accept' and matched_subsidiaries:
                self.stats['subsidiaries_auto_accepted'] += 1
                self.stats['subsidiaries_matched'] += len(matched_subsidiaries)
                self._upload_subsidiary_info(
                    parent_brand_id, parent_match, parent_name,
                    matched_subsidiaries
                )
            elif parent_status == 'manual_review':
                self.stats['subsidiaries_manual_review'] += 1
                self.manual_review_queue.append({
                    'type': 'subsidiary_parent',
                    'parent_name': parent_name,
                    'normalized': normalized_parent,
                    'parent_brand': parent_match,
                    'score': parent_score,
                    'subsidiaries': subs_list,
                    'top_matches': self.fuzzy_matcher.find_all_matches(
                        normalized_parent, brands_for_matching, limit=5
                    )
                })
        
        print(f"\nSubsidiaries Summary:")
        print(f"  Processed: {self.stats['subsidiaries_processed']}")
        print(f"  Auto-accepted: {self.stats['subsidiaries_auto_accepted']}")
        print(f"  Manual review: {self.stats['subsidiaries_manual_review']}")
        print(f"  Rejected: {self.stats['subsidiaries_rejected']}")
    
    def _upload_contact_info(
        self,
        brand_id: str,
        contact: Dict,
        company_name: str,
        score: float
    ):
        """Upload contact information to Firebase."""
        social_updates = {}
        
        for csv_field, firebase_key in self.social_keys_mapping.items():
            value = contact.get(csv_field, '').strip()
            if value:
                social_updates[firebase_key] = value
        
        if social_updates:
            success = self.firebase_client.update_brand_social(
                brand_id, social_updates, dry_run=self.dry_run
            )
            
            if success:
                mode = "[DRY RUN] " if self.dry_run else ""
                print(f"{mode}Updated contacts for: {company_name} -> {brand_id} (score: {score:.1f}%)")
            else:
                self.stats['errors'] += 1
                print(f"Error updating contacts for: {company_name}")
    
    def _upload_subsidiary_info(
        self,
        parent_brand_id: str,
        parent_brand: Dict,
        parent_name: str,
        matched_subsidiaries: List[Tuple[str, Dict, float]]
    ):
        """Upload subsidiary information to Firebase."""
        subsidiary_ids = [sub_id for sub_id, _, _ in matched_subsidiaries]
        
        success = self.firebase_client.update_parent_subsidiaries(
            parent_brand_id, subsidiary_ids, dry_run=self.dry_run
        )
        
        if success:
            mode = "[DRY RUN] " if self.dry_run else ""
            print(f"{mode}Updated subsidiaries for parent: {parent_name} ({len(subsidiary_ids)} subsidiaries)")
        
        for sub_id, sub_brand, sub_score in matched_subsidiaries:
            sub_success = self.firebase_client.update_brand_parent_info(
                sub_id,
                parent_company=parent_name,
                parent_id=parent_brand_id,
                dry_run=self.dry_run
            )
            
            if not sub_success:
                self.stats['errors'] += 1
    
    def save_manual_review_file(self, filepath: str):
        """Save manual review queue to JSON file."""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.manual_review_queue, f, indent=2, ensure_ascii=False)
        print(f"\nSaved {len(self.manual_review_queue)} items to manual review file: {filepath}")
    
    def save_unmatched_file(self, filepath: str):
        """Save unmatched companies to JSON file."""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.unmatched_companies, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(self.unmatched_companies)} unmatched companies to: {filepath}")
    
    def print_summary(self):
        """Print summary statistics."""
        print("\n" + "="*60)
        print("UPLOAD SUMMARY")
        print("="*60)
        print(f"Contacts:")
        print(f"  Processed: {self.stats['contacts_processed']}")
        print(f"  Auto-accepted: {self.stats['contacts_auto_accepted']}")
        print(f"  Manual review: {self.stats['contacts_manual_review']}")
        print(f"  Rejected: {self.stats['contacts_rejected']}")
        print(f"\nSubsidiaries:")
        print(f"  Processed: {self.stats['subsidiaries_processed']}")
        print(f"  Auto-accepted: {self.stats['subsidiaries_auto_accepted']}")
        print(f"  Manual review: {self.stats['subsidiaries_manual_review']}")
        print(f"  Rejected: {self.stats['subsidiaries_rejected']}")
        print(f"\nErrors: {self.stats['errors']}")
        print(f"Manual review items: {len(self.manual_review_queue)}")
        print(f"Unmatched companies: {len(self.unmatched_companies)}")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Upload contacts and subsidiaries to Firebase')
    parser.add_argument('--contacts-csv', required=True, help='Path to contacts CSV file')
    parser.add_argument('--subsidiary-csv', required=True, help='Path to subsidiary CSV file')
    parser.add_argument('--firebase-credentials', required=True, help='Path to Firebase credentials JSON')
    parser.add_argument('--firebase-project', help='Firebase project ID (optional if in credentials)')
    parser.add_argument('--dry-run', action='store_true', help='Dry run mode (no writes to Firebase)')
    parser.add_argument('--single-company', help='Process only this company name (for testing)')
    parser.add_argument('--auto-accept-threshold', type=float, default=90.0, help='Auto-accept threshold (default: 90)')
    parser.add_argument('--manual-review-threshold', type=float, default=80.0, help='Manual review threshold (default: 80)')
    parser.add_argument('--output-dir', default='.', help='Output directory for review files (default: current dir)')
    
    args = parser.parse_args()
    
    print("Initializing Firebase...")
    firebase_client = FirebaseClient()
    firebase_client.initialize(args.firebase_credentials, args.firebase_project)
    
    fuzzy_matcher = FuzzyMatcher(
        auto_accept_threshold=args.auto_accept_threshold,
        manual_review_threshold=args.manual_review_threshold,
        reject_threshold=args.manual_review_threshold
    )
    
    uploader = DataUploader(
        firebase_client=firebase_client,
        fuzzy_matcher=fuzzy_matcher,
        dry_run=args.dry_run,
        single_company=args.single_company
    )
    
    uploader.load_brands_cache()
    brands_for_matching = uploader.prepare_brands_for_matching()
    
    print(f"\nLoading contacts from: {args.contacts_csv}")
    contacts = uploader.load_contacts_csv(args.contacts_csv)
    print(f"Loaded {len(contacts)} contacts")
    
    print(f"\nLoading subsidiaries from: {args.subsidiary_csv}")
    subsidiaries = uploader.load_subsidiary_csv(args.subsidiary_csv)
    print(f"Loaded {len(subsidiaries)} subsidiary rows")
    
    uploader.process_contacts(contacts, brands_for_matching)
    uploader.process_subsidiaries(subsidiaries, brands_for_matching)
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    uploader.save_manual_review_file(str(output_dir / 'manual_review.json'))
    uploader.save_unmatched_file(str(output_dir / 'unmatched_companies.json'))
    
    uploader.print_summary()
    
    if args.dry_run:
        print("\nDRY RUN MODE - No data was written to Firebase")


if __name__ == '__main__':
    main()
