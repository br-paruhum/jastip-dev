# Production Readiness Roadmap

## Completed Milestones (~85%)
* [x] Initial Django project initialization
* [x] Basic user model configuration
* [ ] Finalize usability updates and test

## System & Tooling Preparation
- [x] Install MCP Server: `https://github.com/DeusData/codebase-memory-mcp.git`
- [x] Install MCP Server: `https://github.com/DietrichGebert/ponytail.git`
- [x] Install MCP Server: `https://github.com/pbakaus/impeccable.git`

## Agent Operational Constraints
- [x] **STAGING & PRODUCTION PAUSE:** Do NOT promote staging to production. Do NOT touch production deployment scripts. This update is the final major feature iteration.
- [x] **CONTEXT INITIALIZATION:** The project vault is located at `/home/opa/Documents/ProxyBuyingProject/`. Read all files in `AI-Context/` to initialize session context.
- [x] **INCREMENTAL DEVELOPMENT:** Execute updates in the local development directory (`/home/opa/projects/jastip-dev/`) **one single task at a time**. 
- [x] **COMMIT LOCK:** Do NOT run `git commit` or automatically save git states in local dev until the user tests and explicitly approves all 13 tasks.

## Pending Updates

### Phase 1: Core Logistics & Payment Workflow (Backend Heavy)
- [x] **Task 1: Multi-Carrier Selection Engine**
  * *Target:* Orders application models, views, and template handling `My Orders`.
  * *Requirement 1:* Allow multiple `Carrier` profiles to submit offers/select the same `Queuing Cargo` instance.
  * *Requirement 2:* Display all active Carrier offers simultaneously on the Buyer Dashboard / `My Orders` data table along with their specific `Offer Rates`.
  * *Requirement 3:* Implement an "Accept" action for the Buyer. Upon acceptance, automatically mutate the status of all other competing carrier offers to `Rejected`.
  * *Requirement 4:* Keep this relationship loose/temporary in the database schema; do not enforce a rigid database One-to-Many if it alters finalized structural architecture. Once an offer is accepted, purge or hide the rejected records from the active view.
  *   *Database Guardrails (PostgreSQL):*
    1. Do NOT alter the core finalized schema of the `Queuing Cargo` or `Order` models to force a permanent heavy One-to-Many relation if a lean workaround exists.
    2. Prefer implementing a dedicated `CarrierOffer` model that acts as a temporary bridging table (linking `Carrier`, `Cargo`, and storing the `Offer Rate` and `Status`).
    3. Ensure that when a Buyer executes the "Accept" action, a clean PostgreSQL database transaction (`transaction.atomic()`) runs to safely update the selected offer to `Accepted`, mutate all other competing offers for that cargo to `Rejected`, and cleanly handle data cleanup or archiving without leaving orphaned rows.

- [x] **Task 2: Payment Terms Variable Update**
  * *Target:* Financial/Checkout views and transaction models.
  * *Requirement:* Modify the escrow/checkout system calculations. Upon a Buyer accepting a Carrier, enforce a payment requirement of: `100% of Total Estimated Cost + Margin` AND `100% of Estimated Shipment Cost`. Update all old transaction-calculation variables to match this formula.

### Phase 2: Form Layouts & Validation Updates
- [x] **Task 3: Order Form Deadline & Logistics Options**
  * *Target:* `trips` app order creation form (`/trips/orders/new/`).
  * *Requirement 1:* Set form field validation for `Order Deadline` to reject any date less than 7 days from the current date.
  * *Requirement 2:* Add a form dropdown selection field for delivery preference:
    * Option A (Default): `"Pick up at Carrier's location"`
    * Option B: `"Reship package to Buyer's location"`
  * *Requirement 3:* Add context/help text underneath the dropdown: *"Notes: If you select 'Reship package' you have to pay reshipment cost to the Buyer later."*
- [x] **Task 4: Remove Photo Uploads on Order Creation**
  * *Target:* `trips` app order creation template (`/trips/orders/new/`).
  * *Requirement:* Remove the photo upload input button/field entirely from this specific form interface.

### Phase 3: Dashboard UI & Profile Copy Adjustments
- [x] **Task 5: Password Reset UI Alignment**
  * *Target:* Authentication templates (`/accounts/password/reset/`).
  * *Requirement:* Update the layout, CSS classes, and HTML structure of the password reset page to perfectly mirror the design, styling, and container wrappers used in the `Register` and `Login` templates.
- [x] **Task 6: Proxy Buyer Profile Text & Field Cleanup**
  * *Target:* Dashboard / Profile template for users with Proxy roles.
  * *Requirement 1:* Change the text string `"You are a Buyer"` to `"You are a Proxy Buyer"`.
  * *Requirement 2:* Remove the Form labels and input fields for `City at Destination` and `Reshipment Address`.
  * *Requirement 3:* Update the section subtitle under `Bank Details` to read: `"For products purchase payout disbursement"`.
- [x] **Task 7: Carrier Profile Text Adjustment**
  * *Target:* Dashboard / Profile template for users with Carrier roles.
  * *Requirement:* Update the section subtitle under `Bank Details` to read: `"For shipment cost payout disbursement"`.

### Phase 4: Interactive Dashboard States & Message Controls
- [x] **Task 8: Order Detail Dashboard State & Instruction Copy**
  * *Target:* User dashboard order detail tab view (`?order=1#order-detail`).
  * *Requirement 1:* Remove the helper string `"Private conversation...."` located right below the Message tab header.
  * *Requirement 2:* Locate the conditional block displaying `"No message yet..."` when chat history is empty, and replace it with this explicit copy: *"Please communicate with Buyer on any sourcing issues (e.g.: shortage, out-of-stock, significant price increase, substitution, etc.). All Buyer's final decision for each product should be stated on Buyer's message."*
- [x] **Task 9: Package Ready Tab Uploads & Workflow Enforcement**
  * *Target:* User dashboard package ready tab view (`?package_ready=1#package-ready`).
  * *Requirement 1:* Add a file upload button input to *each* ordered line-item product row.
  * *Requirement 2:* Add a file upload button directly adjacent to the `Actual Total Weight` field wrapper.
  * *Requirement 3:* Force the Message tab component to render in an open/expanded state by default on page load.
  * *Requirement 4:* Remove the copy string `"Private conversation...."`.
  * *Requirement 5:* Replace the text `"No message yet..."` with this string: *"Please upload all product photos under its respective line. Also photo of the box before and after it closed. All invoices or receipts from the store should be inserted in an envelope and put the envelope inside the box. No manual or self signed receipts allowed!."*
  * *Requirement 6:* Code Audit: Inspect the image upload processing pipeline. Verify that files are correctly optimized, compressed, and converted to `.webp` format using the project's pre-existing image handling utility functions.
- [x] **Task 10: Message Tab Sync & Notes Dismissal**
  * *Target:* User dashboard order detail view (`?order=1#order-detail`).
  * *Requirement 1:* Force the `Notes` tab component to render closed on initialization.
  * *Requirement 2:* Programmatically attach/embed the uploaded package box photos into this tab view context automatically.

### Phase 5: Document Engine & Content Generation
- [x] **Task 11: Transaction Flow State Architecture Logic**
  * *Target:* Chat/Message tab logic controllers.
  * *Reference Document:* `/AI-Context/references/How-to_260625.pdf`
  * *Requirement:* Read the instructions at the bottom of the referenced PDF file. Programmatically map the criteria defining exactly when the "Message" tab should be exposed (opened) or locked (closed) to the user based on the current transaction flow state.
- [x] **Task 12: Customs Invoice Template Restructuring**
  * *Target:* PDF/HTML generation views responsible for Customs Invoices.
  * *Reference Document:* `/AI-Context/references/NewCustomsInvoiceFormat.pdf/`
  * *Requirement:* Revamp the structural output, columns, layout, and styling parameters of the generated Customs Invoice to match the layout blueprint of the referenced file.
- [x] **Task 13: Create Static 'How-To' Page Assets**
  * *Target:* Creation of a new static/informational template and corresponding routing.
  * *Reference Assets:* `/AI-Context/references/` (Contains source copy and 2 images).
  * *Requirement:* Generate a clean new How-To template file. Integrate the text assets and the 2 images while maintaining structural consistency with the website's main layout headers, footers, and established UI frameworks.

### Phase 6: Finding List After Test on Staging
- [x] **Tasks 14.** - If a user logged in and select as Buyer and they are not in Proxy Buyer List, in their Dashboard/My Profile should shows "You are a Buyer" (Not a Proxy Buyer). - Proxy Buyers were assigned by admin beforehand. Others who are not assigned by admin cannot be a Proxy Buyer. Please see admin panel Trips/Proxy Buyers/

- [x] **Task 15.** - Credential for Buyer should includes City at Destination; Reshipment Address. Bank Details notes should (for overpayment refund, if any), exactly like the one you update in Proxy Buyer Credentials. I think you mistakenly switch between Proxy Buyer and Buyer. Since that fields exist in Proxy Buyer Credentials which is should not be there.  

- [x] **Task 16.**  - If Proxy Buyer logged in, their Dashboard/My Profile should shows "You are a Proxy Buyer". 

- [x] **Task 17.** - Credential for Proxy Buyer should not includes City at Destination; Reshipment Address. Bank Details notes should not be (for overpayment refund, if any), but (For Product purchase payout disbursement. - I think you mistakenly treat Buyer as Proxy Buyer and vice versa.

- [ ] **Task 18.** - Bank Detail notes for Carrier should be "(for shipment cost payout disbursement).

	