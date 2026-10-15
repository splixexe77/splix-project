/* =============================================================
   SPLIX X CHEAT  —  SITE CONFIGURATION  (js/config.js)
   -------------------------------------------------------------
   >>> EDIT EVERYTHING HERE. <<<
   You normally never need to touch index.html, plus.html or the
   other JS files. Every text, link, price, stat and plan lives
   in this single object.

   HOW TO CUSTOMIZE
   - Links ......: change the string inside the quotes.
                   Use a full URL, e.g. 'https://discord.com/invite/ABC'
                   Leave it as '#' and the site will show a friendly
                   "not configured yet" notice instead of a dead link.
   - Stats ......: numbers are animated automatically.
                   Keep the placeholder strings until you have real ones.
   - Plans ......: add / remove / reorder plan objects freely.
   - Colours ....: the theme lives in css/style.css (:root block).

   FUTURE PAYMENTS
   Each plan has an optional `payment` block reserved for a future
   payment provider. It is intentionally disabled. Nothing here
   collects, stores or transmits payment information.
   ============================================================= */

window.SPLIX = (function () {
  'use strict';

  /* ---------------------------------------------------------
     1. BRAND
     --------------------------------------------------------- */
  const brand = {
    name: 'SPLIX X CHEAT',
    shortName: 'SPLIX',
    tagline: 'YOUR ULTIMATE GAMING COMMUNITY',
    description:
      'Discover gaming tutorials, community updates, useful resources, and the latest Free Fire news.',
    plusName: 'SPLIX PLUS',
    plusTagline: 'LEVEL UP YOUR EXPERIENCE',
    plusDescription:
      'Unlock exclusive community perks and discover a premium experience built for gamers.',
    copyright: 'SPLIX X CHEAT Community',
    disclaimer:
      'This website is an independent gaming community. It is not affiliated with, endorsed by, or connected to Garena Free Fire or Garena in any way.',
  };

  /* ---------------------------------------------------------
     2. SOCIAL / EXTERNAL LINKS
     These are your REAL links and are already live on the site.
     Set any of them back to '#' and the button will show a
     friendly "not configured" notice instead of a dead link.
     --------------------------------------------------------- */
  const links = {
    youtube: 'https://youtube.com/@nxsplix1363?si=AYSnGGuAYatkUOz_',
    discord: 'https://discord.gg/fUbAwWTYC',
    tiktok:  'https://www.tiktok.com/@ghost_hex67?is_from_webapp=1&sender_device=pc',
  };

  /* ---------------------------------------------------------
     2b. PANEL SUBSCRIPTION PRICING
     These are the PRICES OF PANEL — a separate product from the
     FREE / PLUS / ELITE membership tiers in section 8.
     Edit the amounts and labels here; the banner updates itself.
     --------------------------------------------------------- */
  const panelPricing = {
    title: 'PRICE OF PANEL',
    plans: [
      { amount: '8$',  period: 'WEEK',    badge: '',            featured: false, note: '' },
      { amount: '15$', period: 'MONTH',   badge: 'POPULAR',     featured: true,  note: '' },
      { amount: '25$', period: '3 MONTHS',badge: 'BEST VALUE',  featured: false, note: '' },
    ],
    footnote:
      'Panel subscription pricing is separate from the FREE / PLUS / ELITE membership tiers.',
  };

  /* ---------------------------------------------------------
     2c. PAYMENT METHODS
     >>> HONESTY RULE: a method only becomes clickable when you
     set `configured: true` AND fill a real `url`. Until then it
     is clearly shown as NOT CONFIGURED and does nothing.

     No account names, wallet addresses, QR codes, phone numbers
     or payment links are invented here. Fill these yourself
     once each method is genuinely set up and verified.
     --------------------------------------------------------- */
  const paymentMethods = [
    {
      id: 'cashplus',
      name: 'Cash Plus',
      icon: 'cashplus',         // key into the icon map in main.js
      configured: false,
      url: null,                // add your verified Cash Plus link here later
      status: 'NOT CONFIGURED',
    },
    {
      id: 'wafacash',
      name: 'Wafacash',
      icon: 'wafacash',
      configured: false,
      url: null,                // add your verified Wafacash link here later
      status: 'NOT CONFIGURED',
    },
    {
      id: 'bank',
      name: 'Bank Transfer',
      icon: 'bank',
      configured: false,
      url: null,                // add your verified bank-transfer page here later
      status: 'NOT CONFIGURED',
    },
  ];

  /* Payment section heading + safety notice */
  const paymentSection = {
    title: 'PAYMENT METHOD',
    notice:
      'These methods are listed for information only and none of them is active yet. No payment is processed on this website, and no phone number, bank account, recipient name or other financial detail is shown or stored here.',
  };

  /* ---------------------------------------------------------
     3. COMMUNITY STATISTICS
     Placeholder values only. No numbers are invented here.
     Set `value` to a number to enable the count-up animation,
     or keep a string such as 'TBA' / '24/7'.
     --------------------------------------------------------- */
  const stats = [
    { value: 'TBA',  label: 'Community Members', icon: 'fa-solid fa-users' },
    { value: 'TBA',  label: 'Content Updates',   icon: 'fa-solid fa-bolt' },
    { value: 'TBA',  label: 'Tutorials',         icon: 'fa-solid fa-book-open' },
    { value: '24/7', label: 'Support',           icon: 'fa-solid fa-headset' },
  ];

  /* ---------------------------------------------------------
     4. FEATURES
     --------------------------------------------------------- */
  const features = [
    {
      icon: 'fa-solid fa-graduation-cap',
      title: 'Gaming Tutorials',
      text: 'Step-by-step guides that take you from complete beginner to confident player, explained clearly and always up to date.',
    },
    {
      icon: 'fa-solid fa-users',
      title: 'Active Community',
      text: 'A lively, welcoming space full of players who share tips, answer questions and improve together every single day.',
    },
    {
      icon: 'fa-solid fa-gem',
      title: 'Exclusive Content',
      text: 'Members-only resources, setups and guides that you will not find anywhere else on the internet.',
    },
    {
      icon: 'fa-solid fa-headset',
      title: 'Community Support',
      text: 'Stuck on a step? Our support team and experienced members are around to help you get unstuck quickly.',
    },
    {
      icon: 'fa-solid fa-newspaper',
      title: 'Gaming News & Updates',
      text: 'Patch notes, meta changes and community announcements delivered the moment they matter.',
    },
    {
      icon: 'fa-solid fa-shield-halved',
      title: 'Safe & Legitimate',
      text: 'We focus on legitimate strategies, settings and tutorials. Nothing here puts your account at risk.',
    },
  ];

  /* ---------------------------------------------------------
     5. TUTORIALS
     `art` picks one of the generated gradient thumbnails, so
     there are zero external images that could ever break.
     `link` works exactly like the social links above.
     --------------------------------------------------------- */
  const tutorials = [
    {
      art: 'art-1',
      icon: 'fa-solid fa-crosshairs',
      category: 'Beginner',
      title: 'Free Fire Settings Guide',
      text: 'The essential in-game graphics, sensitivity and control settings to get a smooth, stable experience.',
      link: '#',
    },
    {
      art: 'art-2',
      icon: 'fa-solid fa-gauge-high',
      category: 'Setup',
      title: 'BlueStacks Emulator Setup',
      text: 'Configure your emulator the right way for stable performance before you start recording or streaming.',
      link: '#',
    },
    {
      art: 'art-3',
      icon: 'fa-solid fa-video',
      category: 'Recording',
      title: 'OBS Recording Tutorial',
      text: 'A complete OBS walkthrough covering capture, encoder, audio and the best quality settings.',
      link: '#',
    },
    {
      art: 'art-4',
      icon: 'fa-solid fa-map',
      category: 'Gameplay',
      title: 'Landing & Rotation Tips',
      text: 'Where to land, when to rotate and how to survive the early game with a smarter approach.',
      link: '#',
    },
    {
      art: 'art-5',
      icon: 'fa-solid fa-wand-magic-sparkles',
      category: 'Tips',
      title: 'Improving Your Aim',
      text: 'Practical aim routines and habits that actually translate into better fights in ranked matches.',
      link: '#',
    },
    {
      art: 'art-6',
      icon: 'fa-solid fa-fire-flame-curved',
      category: 'News',
      title: 'Latest Free Fire News',
      text: 'Everything worth knowing about recent updates, new content and what it means for your games.',
      link: '#',
    },
  ];

  /* ---------------------------------------------------------
     6. COMMUNITY SECTION
     --------------------------------------------------------- */
  const community = {
    title: 'JOIN OUR COMMUNITY',
    text: 'Connect with the SPLIX X CHEAT community, get notified about new tutorials first, and take part in events and giveaways.',
    bullets: [
      'Friendly, active and helpful members',
      'New tutorials and guides shared regularly',
      'Community events and giveaways',
      'Fast support whenever you need a hand',
    ],
  };

  /* ---------------------------------------------------------
     7. FAQ  (main site)
     --------------------------------------------------------- */
  const faq = [
    {
      q: 'What is the SPLIX X CHEAT community?',
      a: 'SPLIX X CHEAT is an independent Free Fire community. We publish tutorials, settings guides, gameplay tips and news, and we give members a place to connect and help each other improve.',
    },
    {
      q: 'How do I join the community?',
      a: 'Click any "Join Community" button on this site. Once your Discord invite is added in js/config.js, the button will take you straight to the server.',
    },
    {
      q: 'Is this website affiliated with Garena Free Fire?',
      a: 'No. SPLIX X CHEAT is a completely independent community project. We are not affiliated with, endorsed by or connected to Garena Free Fire or Garena.',
    },
    {
      q: 'Is the content safe?',
      a: 'Yes. We only share legitimate strategies, settings, tutorials and news. We do not provide or endorse anything that could put your account at risk.',
    },
    {
      q: 'Do I have to pay for anything?',
      a: 'No. All core tutorials and community access are free. A optional PLUS membership is planned for later, but it is not available yet.',
    },
  ];

  /* ---------------------------------------------------------
     8. MEMBERSHIP PLANS  (plus.html)
     ---------------------------------------------------------
     status:
       'current' -> button is highlighted as the visitor's plan
       'soon'    -> button is disabled and clearly says COMING SOON
       'live'    -> reserved for a future payment provider
     --------------------------------------------------------- */
  const plans = [
    {
      id: 'free',
      name: 'FREE',
      price: 'FREE',
      period: 'forever',
      badge: '',
      featured: false,
      status: 'current',
      buttonText: 'CURRENT PLAN',
      payment: { enabled: false, provider: null, checkoutUrl: null },
      features: [
        'Community access',
        'Standard tutorials',
        'Public updates',
      ],
    },
    {
      id: 'plus',
      name: 'PLUS',
      price: 'COMING SOON',
      period: 'pricing to be announced',
      badge: 'MOST POPULAR',
      featured: true,
      status: 'soon',
      buttonText: 'COMING SOON',
      payment: { enabled: false, provider: null, checkoutUrl: null },
      features: [
        'Exclusive premium content',
        'Advanced tutorials',
        'Priority community support',
        'Early access to new features',
        'Special PLUS member badge',
      ],
    },
    {
      id: 'elite',
      name: 'ELITE',
      price: 'COMING SOON',
      period: 'pricing to be announced',
      badge: '',
      featured: false,
      status: 'soon',
      buttonText: 'COMING SOON',
      payment: { enabled: false, provider: null, checkoutUrl: null },
      features: [
        'Everything in PLUS',
        'Additional exclusive community perks',
        'Special Elite profile badge',
      ],
    },
  ];

  /* ---------------------------------------------------------
     9. MEMBERSHIP COMPARISON TABLE
     `plans` keys must match the plan ids above.
     'yes' = included, 'no' = not included, or a short text.
     --------------------------------------------------------- */
  const comparison = {
    columns: [
      { id: 'free',  label: 'FREE' },
      { id: 'plus',  label: 'PLUS' },
      { id: 'elite', label: 'ELITE' },
    ],
    rows: [
      { label: 'Community access',           free: 'yes', plus: 'yes',  elite: 'yes' },
      { label: 'Standard tutorials',         free: 'yes', plus: 'yes',  elite: 'yes' },
      { label: 'Public updates',             free: 'yes', plus: 'yes',  elite: 'yes' },
      { label: 'Exclusive premium content',  free: 'no',  plus: 'yes',  elite: 'yes' },
      { label: 'Advanced tutorials',         free: 'no',  plus: 'yes',  elite: 'yes' },
      { label: 'Priority support',           free: 'no',  plus: 'yes',  elite: 'yes' },
      { label: 'Early access',               free: 'no',  plus: 'yes',  elite: 'yes' },
      { label: 'PLUS member badge',          free: 'no',  plus: 'yes',  elite: 'yes' },
      { label: 'Exclusive community perks',  free: 'no',  plus: 'no',  elite: 'yes' },
      { label: 'Elite profile badge',        free: 'no',  plus: 'no',  elite: 'yes' },
    ],
  };

  /* ---------------------------------------------------------
     10. MEMBERSHIP FAQ  (plus.html)
     --------------------------------------------------------- */
  const plusFaq = [
    {
      q: 'What is SPLIX PLUS?',
      a: 'SPLIX PLUS is the upcoming premium membership of the SPLIX X CHEAT community. It unlocks exclusive content, advanced tutorials and priority support for members who want to go further.',
    },
    {
      q: 'What benefits do members receive?',
      a: 'PLUS members get exclusive premium content, advanced tutorials, priority community support, early access to new features and a special PLUS member badge. ELITE members additionally receive extra community perks and a special Elite profile badge.',
    },
    {
      q: 'When will memberships become available?',
      a: 'Not yet. Pricing and the launch date have not been announced. Every upgrade button on this page clearly shows COMING SOON, and no payment is processed anywhere on this site.',
    },
    {
      q: 'How can I upgrade?',
      a: 'When memberships launch, the upgrade buttons will become active and will be connected to a secure payment provider. Until then, everything stays free and you do not need to do anything.',
    },
  ];

  /* ---------------------------------------------------------
     11. FUTURE PAYMENT SUPPORT (reserved — disabled)
     When a provider is added later, set `enabled: true` on a
     plan and fill `provider` + `checkoutUrl`. The UI already
     handles 'live' plans. No payment data is ever handled here.
     --------------------------------------------------------- */
  const payments = {
    enabled: false,
    provider: null,      // e.g. 'stripe' | 'paypal' | 'paddle'
    checkoutUrl: null,   // server-hosted, secure checkout endpoint
    notice:
      'Memberships are not available yet. No payment is processed on this website.',
  };

  /* ---------------------------------------------------------
     Expose
     --------------------------------------------------------- */
  return {
    brand,
    links,
    panelPricing,
    paymentMethods,
    paymentSection,
    stats,
    features,
    tutorials,
    community,
    faq,
    plans,
    comparison,
    plusFaq,
    payments,
  };
})();
