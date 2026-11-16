# NeuroIONM Handoff Manual

Επίσημος οδηγός χειρισμού για authenticated χρήστες και admins.

## 1. Login

1. Άνοιξε `https://neuroionm.eu/login.html`.
2. Συμπλήρωσε username και password.
3. Μετά το login ανοίγει το κεντρικό dashboard.
4. Αν ο χρήστης δεν έχει ενεργό session, το σύστημα τον επιστρέφει αυτόματα στο login.

Ο απλός user μπορεί να χρησιμοποιήσει Home, Live, Academy, Manual, Cases και Reports. Η Admin σελίδα ανοίγει μόνο με admin account.

## 2. Admin Vs User

**Admin**

- Δημιουργεί και βλέπει users.
- Ορίζει ή κάνει reset password.
- Ανεβάζει PDF στη Knowledge Base.
- Διαχειρίζεται body-map coordinates.
- Μπορεί να ανοίξει την Admin σελίδα.

**User**

- Μπαίνει στις βασικές προστατευμένες επιφάνειες.
- Χρησιμοποιεί Live, Academy, Cases, Reports, Manual και AI εργαλεία.
- Δεν ανοίγει την Admin σελίδα.
- Δεν μπορεί να δημιουργήσει users ή να ανεβάσει Knowledge Base PDF.

## 3. Δημιουργία Χρήστη

Η δημιουργία χρήστη γίνεται από Admin.

1. Άνοιξε `Admin`.
2. Στο `Create User`, συμπλήρωσε τα υποχρεωτικά πεδία:
   - Username
   - Password
   - First name
   - Last name
   - Email
   - Phone
3. Τα υπόλοιπα πεδία είναι προαιρετικά:
   - Professional title
   - Specialty
   - Organization
   - Department
   - City
   - Country
   - License number
   - Notes
4. Διάλεξε access level:
   - `Learner`: εκπαιδευόμενος / βασικός χρήστης.
   - `Clinician`: κλινικός χρήστης.
   - `Educator`: χρήστης με εκπαιδευτικό ρόλο.
   - `Admin`: πλήρης πρόσβαση στη διαχείριση.
5. Διάλεξε status:
   - `Active`: μπορεί να κάνει login.
   - `Pending`: υπάρχει σαν profile, αλλά περιμένει ενεργοποίηση/έλεγχο.
   - `Inactive`: δεν πρέπει να χρησιμοποιείται για ενεργή πρόσβαση.
6. Πάτησε `Create user`.

Ο admin μπορεί να πατήσει `Generate password` για να παραχθεί ισχυρό password. Το password εμφανίζεται μόνο κατά τη δημιουργία ή το reset και πρέπει να δοθεί στον χρήστη από ασφαλές κανάλι.

## 4. Reset Password

1. Άνοιξε `Admin`.
2. Πήγαινε στη λίστα `Users`.
3. Βρες τον χρήστη.
4. Πάτησε `Reset password`.
5. Επιβεβαίωσε την ενέργεια.
6. Το νέο password εμφανίζεται στην οθόνη του admin.
7. Δώσε το νέο password στον χρήστη.

Μετά το reset, ο χρήστης κάνει login με το νέο password. Το παλιό password δεν ισχύει.

## 5. Knowledge Base

Η Knowledge Base είναι η βιβλιοθήκη επίσημων εγγράφων που χρησιμοποιεί το σύστημα για evidence-aware απαντήσεις και αναζήτηση. Περιλαμβάνει PDFs όπως πρωτόκολλα, manuals, clinical SOPs, οδηγοί χρήσης, εκπαιδευτικά έγγραφα και επιστημονικά references.

Η Knowledge Base δεν είναι απλός φάκελος αρχείων. Όταν ανέβει PDF:

1. Το σύστημα διαβάζει το αρχείο.
2. Εξάγει κείμενο με OCR όταν χρειάζεται.
3. Χωρίζει το περιεχόμενο σε τμήματα.
4. Δημιουργεί searchable/indexed knowledge chunks.
5. Τα AI εργαλεία μπορούν να ανακτούν σχετικά αποσπάσματα όταν απαντούν.

Τι να ανεβαίνει:

- Επίσημα IONM protocols.
- Internal SOPs και checklists.
- Training manuals.
- Device/procedure guidance που επιτρέπεται να χρησιμοποιηθεί στο σύστημα.
- Published papers ή guidelines που θέλετε να εμφανίζονται ως evidence.

Τι να αποφεύγεται:

- Πρόχειρα ή μη εγκεκριμένα κείμενα.
- Διπλότυπα PDFs χωρίς λόγο.
- Έγγραφα με προσωπικά στοιχεία ασθενών.
- Έγγραφα που δεν θέλετε να επηρεάζουν AI απαντήσεις.

## 6. Knowledge Base Upload

1. Άνοιξε `Admin`.
2. Πήγαινε στο `AI Knowledge Base`.
3. Συμπλήρωσε `Title` με καθαρό τίτλο, π.χ. `Spine IONM Protocol`.
4. Συμπλήρωσε `Domain`, π.χ. `spine`, `cranial`, `thyroid`, `ssep`, `pedicle`, `general`.
5. Πρόσθεσε tags χωρισμένα με κόμμα, π.χ. `MEP, EMG, spine`.
6. Διάλεξε PDF.
7. Πάτησε `Upload and index`.
8. Περίμενε να ολοκληρωθεί το upload/indexing.
9. Έλεγξε ότι το έγγραφο εμφανίζεται στο `Knowledge Documents`.

Καλή πρακτική: ανεβάζουμε πρώτα λίγα, επίσημα, καλά ονομασμένα έγγραφα και ελέγχουμε τις AI απαντήσεις πριν γεμίσουμε μαζικά τη βάση.

## 7. Live Workspace

Το Live είναι το βασικό clinical workstation.

1. Άνοιξε `Live`.
2. Διάλεξε περιοχή/διαδικασία από το decision tree.
3. Έλεγξε το επιλεγμένο node: anatomy, roots/cranial nerves, channels, body-map coverage και readiness.
4. Δες τα προτεινόμενα channels και την τοποθέτηση ηλεκτροδίων.
5. Άνοιξε ή δημιούργησε case όταν θέλεις να καταγράψεις πραγματική ροή εργασίας.
6. Κατέγραψε events στο timeline με καθαρή περιγραφή.
7. Χρησιμοποίησε AI μόνο ως υποστήριξη reasoning και τεκμηρίωσης.

Βασικά στοιχεία του Live:

- `Decision tree`: επιλογή procedure/anatomy path.
- `Readiness`: δείχνει αν το node έχει αρκετή κάλυψη ή χρειάζεται review.
- `Channel plan`: προτεινόμενοι μύες/κανάλια και σχετική ανατομία.
- `Body map`: οπτική ένδειξη τοποθέτησης ηλεκτροδίων.
- `Case timeline`: καταγραφή σημαντικών γεγονότων.
- `Clinical Copilot`: structured AI βοήθεια για το επιλεγμένο context.

## 8. Cases

Τα Cases είναι οργανωμένες εγγραφές περιστατικών/σεναρίων μέσα στην πλατφόρμα.

Ένα case χρησιμοποιείται για:

- παρακολούθηση timeline,
- αποθήκευση σημαντικών events,
- σύνδεση Live context με AI debrief,
- παραγωγή report,
- review μετά το περιστατικό.

Για νέο case:

1. Άνοιξε `Live` ή `Cases`.
2. Δημιούργησε case με μη αναγνωριστικά στοιχεία.
3. Διάλεξε procedure/context.
4. Κατέγραψε timeline events.
5. Ζήτησε debrief όταν υπάρχει αρκετή πληροφορία.
6. Άνοιξε το case από `Cases` για review.

## 9. Report

Το Report είναι η αναγνώσιμη παρουσίαση ενός case.

1. Άνοιξε `Cases`.
2. Επίλεξε case.
3. Πάτησε το report link/κουμπί.
4. Έλεγξε case summary, timeline, AI debrief και recommendations.
5. Χρησιμοποίησε browser print/PDF όταν χρειάζεται εξαγωγή.

Το Report δεν αντικαθιστά επίσημο ιατρικό φάκελο. Είναι εργαλείο review, τεκμηρίωσης και handoff μέσα στη ροή της πλατφόρμας.

## 10. Debrief

Το Debrief είναι AI-assisted ανασκόπηση case.

Χρησιμοποιείται για:

- σύνοψη γεγονότων,
- αναγνώριση κρίσιμων σημείων,
- προτάσεις follow-up,
- εκπαιδευτική ανατροφοδότηση,
- σύνδεση timeline με available evidence.

Για καλύτερο debrief:

1. Κατέγραψε καθαρά events στο case timeline.
2. Χρησιμοποίησε συγκεκριμένες περιγραφές.
3. Απόφυγε ασαφείς σημειώσεις όπως `issue happened`.
4. Ζήτησε debrief μετά από αρκετά events.
5. Διάβασε το αποτέλεσμα ως βοηθητική ανάλυση, όχι ως αυτόματη κλινική απόφαση.

## 11. Academy

Το Academy είναι εκπαιδευτικό workspace.

Κύριες ενότητες:

- `Lesson`: objectives, competencies, checklist, checkpoints και case scenarios.
- `Placement`: simulation τοποθέτησης ηλεκτροδίων στον body map.
- `Waveforms`: εκπαιδευτικά waveforms με ρυθμιζόμενες παραμέτρους.
- `Tutor`: AI tutor για το επιλεγμένο lesson.
- `Evidence`: βιβλιογραφία και evidence για το lesson.

Βασική ροή:

1. Διάλεξε learner.
2. Πάτησε `Start Academy` ή `Continue`.
3. Διάβασε το lesson.
4. Ολοκλήρωσε checklist.
5. Κάνε placement lab όπου υπάρχει.
6. Άνοιξε Waveforms και άλλαξε παραμέτρους όταν χρειάζεται.
7. Ολοκλήρωσε waveform quiz.
8. Ολοκλήρωσε case scenarios.
9. Χρησιμοποίησε Tutor για στοχευμένες ερωτήσεις.
10. Έλεγξε progress και review recommendations.

## 12. Waveform Parameters

Στο Academy Waveforms, οι βασικές παράμετροι είναι:

- `Amplitude drop`: πόσο πέφτει το signal amplitude σε warning scenario.
- `Latency shift`: πόσο μετατοπίζεται ο χρόνος απόκρισης.
- `Threshold`: ένταση/όριο stimulation όπου εμφανίζεται response.
- `Noise`: πόσο θόρυβο εμφανίζει το εκπαιδευτικό σήμα.

Αλλάζεις παραμέτρους για να δεις πώς μεταβάλλεται το training scenario. Μετά πατάς apply/update για να φορτωθούν τα νέα waveforms.

## 13. AI Tools

Το AI layer βοηθά σε ερωτήσεις, clinical reasoning, tutor feedback και debrief.

Πώς λειτουργεί σε επίπεδο χειρισμού:

1. Ο χρήστης επιλέγει context, π.χ. lesson, node ή case.
2. Το σύστημα μαζεύει σχετικές πληροφορίες από planner, Knowledge Base, protocols, bibliography και case timeline.
3. Το AI παράγει απάντηση με βάση αυτό το context.
4. Ο χρήστης ελέγχει την απάντηση και την χρησιμοποιεί ως υποστήριξη.

Καλές ερωτήσεις:

- `Τι πρέπει να ελέγξω πριν θεωρήσω αυτή την απώλεια MEP σημαντική;`
- `Ποια είναι τα τεχνικά αίτια που πρέπει να αποκλείσω;`
- `Ποια channels συνδέονται με αυτό το root;`
- `Τι λείπει από αυτό το case timeline πριν ζητήσω debrief;`

## 14. Body Map Curation

Η Body Map Curation είναι admin εργαλείο για διόρθωση/επιβεβαίωση electrode coordinates.

1. Άνοιξε `Admin`.
2. Πήγαινε στο `Body Map Curation`.
3. Διάλεξε muscle/target.
4. Διάλεξε pin.
5. Κάνε click στον χάρτη για νέα θέση.
6. Πάτησε `Save coordinate`.
7. Χρησιμοποίησε `Reset target from seed` μόνο αν θέλεις επιστροφή στη βασική θέση.
8. Χρησιμοποίησε `Export JSON` όταν ολοκληρωθεί review.

Οι αλλαγές εδώ επηρεάζουν Live και Academy placement. Πρέπει να γίνονται με προσοχή.

## 15. Daily Operating Flow

Προτεινόμενη σειρά χρήσης:

1. Admin δημιουργεί users.
2. Admin ανεβάζει επίσημα Knowledge Base PDFs.
3. User ανοίγει Academy για εκπαίδευση.
4. User ανοίγει Live για clinical workflow.
5. User δημιουργεί case όταν χρειάζεται καταγραφή.
6. User καταγράφει timeline events.
7. User ζητά debrief.
8. User ανοίγει report για review/export.
9. Admin ελέγχει users, KB documents και body-map curation όταν χρειάζεται.

## 16. Acceptance Checklist

Πριν θεωρηθεί ολοκληρωμένη η παράδοση λειτουργίας:

1. Login admin επιτυχημένο.
2. Login normal user επιτυχημένο.
3. Normal user δεν ανοίγει Admin page.
4. Admin δημιουργεί user με username, password, first name, last name, email και phone.
5. Admin κάνει reset password.
6. Knowledge Base upload εμφανίζει το PDF στα documents.
7. Live ανοίγει node και εμφανίζει channel/body-map context.
8. Case δημιουργείται και δέχεται timeline events.
9. Debrief παράγεται και αποθηκεύεται.
10. Report ανοίγει για το case.
11. Academy lesson ανοίγει και αποθηκεύει learner progress.
12. AI Tutor απαντά στο επιλεγμένο lesson context.