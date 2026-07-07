// Idempotent MongoDB user provisioning for FuzePlan consumers.
// Run with: mongosh -u admin -p <password> --authenticationDatabase admin 00-fuzeplan.js
// Safe to re-run: creates users only if they do not already exist.

(function() {
  const db = connect('mongodb://localhost:27017/FuzePlan');
  const existing = db.getUsers().users.map(u => u.user);

  const users = [
    { user: 'fuzeplan_app', roles: [{ role: 'readWrite', db: 'FuzePlan' }] },
    { user: 'claude_runner_user', roles: [{ role: 'readWrite', db: 'FuzePlan' }] },
  ];

  users.forEach(({ user, roles }) => {
    if (existing.includes(user)) {
      print('skip: ' + user + ' already exists');
      return;
    }
    const pwd = process.env[user.toUpperCase() + '_PASSWORD'] || (() => { throw new Error('env ' + user.toUpperCase() + '_PASSWORD not set'); })();
    db.createUser({ user, pwd, roles });
    print('created: ' + user);
  });
})();
