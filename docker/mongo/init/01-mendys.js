// Idempotent MongoDB user provisioning for MendysRobotics consumers.
// Run with: mongosh -u admin -p <password> --authenticationDatabase admin 01-mendys.js
// Safe to re-run: creates users only if they do not already exist.
//
// Password is sourced from the environment:
//   MENDYS_APP_PASSWORD — the MendysRobotics app user password
// Set this variable in the runner before invoking this script (never hardcode).

(function() {
  const db = connect('mongodb://localhost:27017/mendys');
  const existing = db.getUsers().users.map(u => u.user);

  const users = [
    { user: 'mendys_app', roles: [{ role: 'readWrite', db: 'mendys' }] },
  ];

  users.forEach(({ user, roles }) => {
    if (existing.includes(user)) {
      print('skip: ' + user + ' already exists');
      return;
    }
    const envKey = user.toUpperCase() + '_PASSWORD';
    const pwd = process.env[envKey] || (() => { throw new Error('env ' + envKey + ' not set'); })();
    db.createUser({ user, pwd, roles });
    print('created: ' + user);
  });
})();
