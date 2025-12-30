"""
Tool for reviewing matches that need a human to check.
Lets you go through the list and approve or reject matches.
"""
import json
import sys
from typing import Dict, List
from uploader import FirebaseClient


def load_review_file(filepath: str) -> List[Dict]:
    """Load manual review JSON file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def display_review_item(item: Dict, index: int, total: int):
    """Display a review item with options."""
    print("\n" + "="*60)
    print(f"Item {index + 1} of {total}")
    print("="*60)
    
    item_type = item.get('type', 'unknown')
    score = item.get('score', 0)
    
    if item_type == 'contact':
        company_name = item.get('company_name', '')
        brand_match = item.get('brand_match', {})
        brand_name = brand_match.get('name', brand_match.get('company_name', 'Unknown'))
        brand_id = brand_match.get('brand_id', 'Unknown')
        
        print(f"Type: Contact Information")
        print(f"Company: {company_name}")
        print(f"Match: {brand_name} (ID: {brand_id})")
        print(f"Similarity Score: {score:.1f}%")
        print(f"\nContact Data:")
        contact_data = item.get('contact_data', {})
        for key, value in contact_data.items():
            if value:
                print(f"  {key}: {value}")
        
        print(f"\nTop Alternative Matches:")
        top_matches = item.get('top_matches', [])
        for i, (alt_brand, alt_score) in enumerate(top_matches[:3], 1):
            alt_name = alt_brand.get('name', alt_brand.get('company_name', 'Unknown'))
            alt_id = alt_brand.get('brand_id', 'Unknown')
            print(f"  {i}. {alt_name} (ID: {alt_id}) - {alt_score:.1f}%")
    
    elif item_type == 'subsidiary':
        parent_name = item.get('parent_name', '')
        subsidiary_name = item.get('subsidiary_name', '')
        parent_brand = item.get('parent_brand', {})
        subsidiary_brand = item.get('subsidiary_brand', {})
        
        print(f"Type: Subsidiary Relationship")
        print(f"Parent: {parent_name}")
        print(f"Subsidiary: {subsidiary_name}")
        print(f"Parent Match: {parent_brand.get('name', 'Unknown')} (ID: {parent_brand.get('brand_id', 'Unknown')})")
        print(f"Subsidiary Match: {subsidiary_brand.get('name', 'Unknown')} (ID: {subsidiary_brand.get('brand_id', 'Unknown')})")
        print(f"Similarity Score: {score:.1f}%")
    
    elif item_type == 'subsidiary_parent':
        parent_name = item.get('parent_name', '')
        parent_brand = item.get('parent_brand', {})
        subsidiaries = item.get('subsidiaries', [])
        
        print(f"Type: Subsidiary Parent")
        print(f"Parent: {parent_name}")
        print(f"Parent Match: {parent_brand.get('name', 'Unknown')} (ID: {parent_brand.get('brand_id', 'Unknown')})")
        print(f"Similarity Score: {score:.1f}%")
        print(f"Subsidiaries: {len(subsidiaries)}")
    
    print("\nOptions:")
    print("  [a] Accept match")
    print("  [r] Reject match")
    print("  [s] Skip (review later)")
    print("  [q] Quit and save progress")


def process_review_item(
    item: Dict,
    choice: str,
    firebase_client: FirebaseClient,
    dry_run: bool = False
) -> bool:
    """
    Process a review item based on user choice.
    
    Returns:
        True if item was processed, False if skipped
    """
    if choice.lower() == 's':
        return False  # Skip
    
    if choice.lower() == 'r':
        print("Match rejected - will be added to unmatched list")
        return True  # Processed (rejected)
    
    if choice.lower() == 'a':
        item_type = item.get('type')
        
        if item_type == 'contact':
            brand_id = item.get('brand_match', {}).get('brand_id')
            contact_data = item.get('contact_data', {})
            
            # Map contact fields to social keys (same as in uploader)
            social_keys_mapping = {
                'twitter_url': 'twitter',
                'facebook_url': 'facebook',
                'bluesky_url': 'bluesky',
                'ir_email': 'ir_email',
                'cs_email': 'cs_email',
                'ir_page': 'ir_page',
                'cs_page': 'cs_page',
                'domain': 'website',
            }
            
            social_updates = {}
            for csv_field, firebase_key in social_keys_mapping.items():
                value = contact_data.get(csv_field, '').strip()
                if value:
                    social_updates[firebase_key] = value
            
            if social_updates:
                success = firebase_client.update_brand_social(
                    brand_id, social_updates, dry_run=dry_run
                )
                if success:
                    mode = "[DRY RUN] " if dry_run else ""
                    print(f"{mode}Contact info updated for brand {brand_id}")
                else:
                    print(f"Error updating contact info")
                    return False
        
        elif item_type == 'subsidiary':
            parent_brand_id = item.get('parent_brand', {}).get('brand_id')
            subsidiary_brand_id = item.get('subsidiary_brand', {}).get('brand_id')
            parent_name = item.get('parent_name', '')
            
            # Update parent's subsidiaries map
            firebase_client.update_parent_subsidiaries(
                parent_brand_id, [subsidiary_brand_id], dry_run=dry_run
            )
            
            # Update subsidiary's parent info
            firebase_client.update_brand_parent_info(
                subsidiary_brand_id,
                parent_company=parent_name,
                parent_id=parent_brand_id,
                dry_run=dry_run
            )
            
            mode = "[DRY RUN] " if dry_run else ""
            print(f"{mode}Subsidiary relationship updated")
        
        return True
    
    return False


def main():
    """Main entry point for review tool."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Interactive manual review tool')
    parser.add_argument('--review-file', required=True, help='Path to manual_review.json file')
    parser.add_argument('--firebase-credentials', required=True, help='Path to Firebase credentials JSON')
    parser.add_argument('--firebase-project', help='Firebase project ID')
    parser.add_argument('--dry-run', action='store_true', help='Dry run mode')
    parser.add_argument('--output-file', help='Output file for remaining items (default: overwrite input)')
    
    args = parser.parse_args()
    
    # Load review items
    print(f"Loading review items from: {args.review_file}")
    review_items = load_review_file(args.review_file)
    print(f"Loaded {len(review_items)} items for review")
    
    # Initialize Firebase
    firebase_client = FirebaseClient()
    firebase_client.initialize(args.firebase_credentials, args.firebase_project)
    
    # Process items
    remaining_items = []
    processed_count = 0
    
    for i, item in enumerate(review_items):
        display_review_item(item, i, len(review_items))
        
        while True:
            choice = input("\nEnter choice [a/r/s/q]: ").strip().lower()
            
            if choice == 'q':
                # Save remaining items and exit
                remaining_items.extend(review_items[i:])
                output_file = args.output_file or args.review_file
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(remaining_items, f, indent=2, ensure_ascii=False)
                print(f"\nSaved {len(remaining_items)} remaining items to {output_file}")
                sys.exit(0)
            
            if choice in ['a', 'r', 's']:
                processed = process_review_item(item, choice, firebase_client, args.dry_run)
                if processed:
                    processed_count += 1
                else:
                    remaining_items.append(item)
                break
            
            print("Invalid choice. Please enter 'a', 'r', 's', or 'q'")
    
    # Save any remaining items
    output_file = args.output_file or args.review_file
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(remaining_items, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'='*60}")
    print(f"Review Complete")
    print(f"{'='*60}")
    print(f"Processed: {processed_count}")
    print(f"Remaining: {len(remaining_items)}")
    print(f"Saved remaining items to: {output_file}")
    
    if args.dry_run:
        print("\nDRY RUN MODE - No data was written to Firebase")


if __name__ == '__main__':
    main()

